import io

import pytest
from PIL import Image

from f1rag import store
from f1rag.ingest import headshots

# Everything below is placeholder data for tests, not real F1 data or real images


def png_bytes(size=(93, 93), color=(200, 30, 30)):
    """Make a small plain PNG in memory"""
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeResponse:
    """A minimal HTTP response"""

    def __init__(self, status=200, content=b"", content_type="image/png"):
        """Keep the status, body and content type"""
        self.status_code = status
        self.content = content
        self.headers = {"Content-Type": content_type}


class FakeHttp:
    """Answers each URL with a prepared response and records the requests"""

    def __init__(self, answers):
        """Keep the answers by URL"""
        self.answers = answers
        self.requests = []

    def get(self, url, headers=None, timeout=None):
        """Record the request and return the prepared answer"""
        self.requests.append((url, headers, timeout))
        return self.answers[url]


URL_A = "https://example.invalid/aaa.png"
URL_A_OLD = "https://example.invalid/aaa-old.png"
URL_B = "https://example.invalid/bbb.png"


def good_http():
    """Answer both photo links with a valid image"""
    return FakeHttp({url: FakeResponse(content=png_bytes()) for url in (URL_A, URL_B)})


@pytest.fixture
def conn():
    """Build entries for three placeholder drivers over two rounds"""
    c = store.connect(":memory:")
    store.create_schema(c)
    rows = [
        (2026, 1, "AAA", "Driver Alpha", "Placeholder Team A", "112233", URL_A_OLD),
        (2026, 2, "AAA", "Driver Alpha", "Placeholder Team A", "112233", URL_A),
        (2026, 2, "BBB", "Driver Beta", "Placeholder Team B", "445566", URL_B),
        (2026, 2, "CCC", "Driver Gamma", "Placeholder Team B", "445566", ""),
        (2025, 5, "DDD", "Driver Delta", "Placeholder Team D", "778899", URL_B),
    ]
    with c:
        c.executemany(
            "INSERT INTO entries (season, round, driver_code, full_name, team, team_color, "
            "headshot_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    yield c
    c.close()


def test_each_driver_gets_the_latest_photo_link_of_the_season(conn):
    """Check one row per driver of the season, with the link from the latest race"""
    drivers = {d["code"]: d for d in headshots.season_drivers(conn, 2026)}
    assert sorted(drivers) == ["AAA", "BBB", "CCC"]
    assert drivers["AAA"]["url"] == URL_A
    assert drivers["AAA"]["team"] == "Placeholder Team A"
    assert drivers["CCC"]["url"] == ""


def test_photos_are_saved_by_driver_code_and_missing_links_reported(conn, tmp_path):
    """Check each photo is saved as CODE.png and a driver with no link is listed as missing"""
    http = good_http()
    report = headshots.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None)
    assert sorted(p.name for p in tmp_path.glob("*.png")) == ["AAA.png", "BBB.png"]
    assert report["saved"] == ["AAA", "BBB"]
    assert report["missing"] == ["CCC"]
    assert report["failed"] == []
    assert all(h["User-Agent"].startswith("f1-race-rag-chatbot/") for _, h, _ in http.requests)


def test_existing_photos_are_kept_unless_refresh(conn, tmp_path):
    """Check a second run downloads nothing, and refresh downloads again"""
    http = good_http()
    headshots.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None)
    again = headshots.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None)
    assert again["kept"] == ["AAA", "BBB"] and len(http.requests) == 2
    headshots.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None, refresh=True)
    assert len(http.requests) == 4


def test_downloads_are_paced(conn, tmp_path):
    """Check there is a pause before every download after the first"""
    waits = []
    http = good_http()
    headshots.download_all(conn, 2026, tmp_path, http=http, sleep=waits.append)
    assert waits == [headshots.PACE_S]


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(status=404),
        FakeResponse(content=b"<html>not an image</html>", content_type="text/html"),
        FakeResponse(content=b"not really a png"),
        FakeResponse(content=b"x" * (headshots.MAX_BYTES + 1)),
    ],
)
def test_bad_answers_are_reported_and_not_saved(conn, tmp_path, response):
    """Check an error, a page that is not an image or an oversized file saves nothing"""
    http = FakeHttp({URL_A: response, URL_B: FakeResponse(content=png_bytes())})
    report = headshots.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None)
    assert [code for code, _ in report["failed"]] == ["AAA"]
    assert not (tmp_path / "AAA.png").exists()


def test_photos_are_made_square_from_the_top():
    """Check a tall photo is cut to a square that keeps the top, where the head is"""
    tall = Image.new("RGB", (60, 100), (0, 0, 0))
    tall.paste((255, 255, 255), (0, 0, 60, 10))
    square = headshots.make_square(tall)
    assert square.size == (60, 60)
    assert square.getpixel((5, 5)) == (255, 255, 255)


def test_driver_codes_that_are_not_three_letters_are_refused():
    """Check a code that could escape the folder never becomes a file name"""
    with pytest.raises(ValueError):
        headshots.photo_path("../x", "/tmp")


def test_review_sheet_shows_every_driver(conn, tmp_path):
    """Check the review sheet is an image with one tile per driver, missing photos included"""
    http = good_http()
    headshots.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None)
    sheet = headshots.review_sheet(conn, 2026, tmp_path)
    image = Image.open(sheet)
    columns = min(3, headshots.SHEET_COLUMNS)
    assert sheet.name == "review_sheet.png"
    assert image.size[0] == columns * headshots.TILE_W
    assert image.size[1] == headshots.SHEET_TOP + headshots.TILE_H


def test_skipped_drivers_are_removed_and_never_downloaded(conn, tmp_path):
    """Check a driver on the skip list loses the saved photo and is not fetched again"""
    http = good_http()
    headshots.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None)
    headshots.add_to_skip_list(tmp_path, ["aaa"])
    report = headshots.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None,
                                    refresh=True)
    assert report["skipped"] == ["AAA"]
    assert not (tmp_path / "AAA.png").exists()
    assert [url for url, _, _ in http.requests].count(URL_A) == 1
    assert headshots.read_skip_list(tmp_path) == {"AAA"}


def test_skip_list_refuses_codes_that_are_not_drivers(tmp_path):
    """Check only three-letter codes go on the skip list"""
    with pytest.raises(ValueError):
        headshots.add_to_skip_list(tmp_path, ["../etc"])


def cut_out(size, head_box, body_box):
    """Draw a placeholder cut-out: a transparent picture with a head and shoulders"""
    from PIL import ImageDraw

    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(head_box, fill=(200, 160, 120, 255))
    draw.rectangle(body_box, fill=(20, 40, 160, 255))
    return image


def head_of(image):
    """Measure the head in a picture: top, centre and width"""
    return headshots.find_head(image.getchannel("A"))


@pytest.mark.parametrize(
    "size, head_box, body_box",
    [
        ((93, 93), (40, 6, 72, 40), (20, 40, 93, 93)),
        ((93, 93), (8, 1, 38, 33), (0, 33, 60, 93)),
        ((440, 1265), (170, 20, 260, 130), (110, 130, 330, 1265)),
    ],
)
def test_heads_are_centred_with_room_above_the_hair(size, head_box, body_box):
    """Check off-centre heads end in the middle, at the same size, with a gap above the hair"""
    avatar = headshots.centre_on_head(cut_out(size, head_box, body_box))
    assert avatar.size == (headshots.PHOTO_SIZE, headshots.PHOTO_SIZE)
    top, centre, width = head_of(avatar)
    side = headshots.PHOTO_SIZE
    assert abs(centre - side / 2) <= 2
    assert abs(top - headshots.TOP_SHARE * side) <= 3
    assert abs(width - headshots.HEAD_SHARE * side) <= 0.06 * side


def test_a_picture_with_nobody_in_it_is_refused():
    """Check a fully transparent picture is refused instead of cropped"""
    with pytest.raises(ValueError):
        headshots.centre_on_head(Image.new("RGBA", (93, 93), (0, 0, 0, 0)))


URL_C = "https://example.invalid/ccc.png"


def test_a_manual_link_fills_a_driver_with_no_photo_link(conn, tmp_path):
    """Check a link added by hand is used for a driver whose race data has none"""
    headshots.add_link(tmp_path, "ccc", URL_C)
    http = FakeHttp({url: FakeResponse(content=png_bytes()) for url in (URL_A, URL_B, URL_C)})
    report = headshots.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None)
    assert report["saved"] == ["AAA", "BBB", "CCC"]
    assert report["missing"] == []
    assert (tmp_path / "CCC.png").exists()


def test_a_manual_link_replaces_a_rejected_photo(conn, tmp_path):
    """Check a new link takes a driver off the skip list and downloads the new photo"""
    http = FakeHttp({url: FakeResponse(content=png_bytes()) for url in (URL_A, URL_B, URL_C)})
    headshots.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None)
    headshots.add_to_skip_list(tmp_path, ["AAA"])
    headshots.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None)
    headshots.add_link(tmp_path, "AAA", URL_C)
    assert headshots.read_skip_list(tmp_path) == set()
    assert headshots.read_links(tmp_path) == {"AAA": URL_C}
    report = headshots.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None)
    assert report["saved"] == ["AAA"]
    assert http.requests[-1][0] == URL_C


@pytest.mark.parametrize("code, url", [("../x", URL_C), ("AAA", "ftp://example.invalid/a.png")])
def test_manual_links_must_be_a_driver_code_and_a_web_address(tmp_path, code, url):
    """Check bad codes and non-https links are refused"""
    with pytest.raises(ValueError):
        headshots.add_link(tmp_path, code, url)


def test_an_adjustment_zooms_in_and_moves_one_driver_down(tmp_path):
    """Check a per-driver zoom makes the head larger and a shift moves it lower"""
    picture = cut_out((93, 93), (30, 6, 62, 40), (10, 40, 83, 93))
    plain = head_of(headshots.centre_on_head(picture))
    adjusted = head_of(headshots.centre_on_head(picture, zoom=1.2, down=0.05))
    assert adjusted[2] >= plain[2] * 1.15
    assert adjusted[0] > plain[0] + 0.04 * headshots.PHOTO_SIZE
    assert abs(adjusted[1] - headshots.PHOTO_SIZE / 2) <= 2


def test_adjustments_are_stored_and_used_for_that_driver_only(conn, tmp_path):
    """Check an adjustment is saved by driver code and changes only that driver's avatar"""
    buffer = io.BytesIO()
    cut_out((93, 93), (30, 6, 62, 40), (10, 40, 83, 93)).save(buffer, format="PNG")
    http = FakeHttp({url: FakeResponse(content=buffer.getvalue()) for url in (URL_A, URL_B)})
    headshots.add_adjustment(tmp_path, "aaa", 1.1, 0.03)
    assert headshots.read_adjustments(tmp_path) == {"AAA": (1.1, 0.03)}
    seen = []
    original = headshots.centre_on_head

    def spy(image, zoom=1.0, down=0.0):
        """Record the adjustment each avatar is cropped with"""
        seen.append((zoom, down))
        return original(image, zoom, down)

    headshots.centre_on_head = spy
    try:
        headshots.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None)
    finally:
        headshots.centre_on_head = original
    assert sorted(seen) == [(1.0, 0.0), (1.1, 0.03)]


@pytest.mark.parametrize("zoom, down", [(0.5, 0.0), (2.0, 0.0), (1.1, 0.5), (1.1, -0.5)])
def test_adjustments_outside_safe_limits_are_refused(tmp_path, zoom, down):
    """Check a zoom or shift big enough to cut the hair or lose the face is refused"""
    with pytest.raises(ValueError):
        headshots.add_adjustment(tmp_path, "AAA", zoom, down)
