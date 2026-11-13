import io

import pytest
from PIL import Image

from f1rag import store
from f1rag.ingest import team_logos

# Everything below is placeholder data for tests, not real F1 data or real images


def png_bytes():
    """Make a small transparent PNG in memory"""
    buffer = io.BytesIO()
    Image.new("RGBA", (40, 20), (255, 255, 255, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeResponse:
    """A minimal HTTP response"""

    def __init__(self, status=200, content=b"", content_type="image/webp"):
        """Keep the status, body and content type"""
        self.status_code = status
        self.content = content
        self.headers = {"Content-Type": content_type}


class FakeHttp:
    """Answers every logo link with the same response and records the links"""

    def __init__(self, response):
        """Keep the response to give"""
        self.response = response
        self.urls = []

    def get(self, url, headers=None, timeout=None):
        """Record the link and return the prepared answer"""
        self.urls.append(url)
        return self.response


@pytest.fixture
def conn():
    """Build entries for two placeholder teams in one season"""
    c = store.connect(":memory:")
    store.create_schema(c)
    with c:
        c.executemany(
            "INSERT INTO entries (season, round, driver_code, team, team_color) "
            "VALUES (2026, 1, ?, ?, '112233')",
            [("AAA", "Team Alpha"), ("BBB", "Team Alpha"), ("CCC", "Haas F1 Team")],
        )
    yield c
    c.close()


def test_team_names_become_the_official_link_pattern():
    """Check a team name becomes the slug and logo link the official site uses"""
    assert team_logos.slug("Haas F1 Team") == "haasf1team"
    assert team_logos.slug("Red Bull Racing") == "redbullracing"
    url = team_logos.logo_url(2026, "Red Bull Racing")
    assert url.endswith("/common/f1/2026/redbullracing/2026redbullracinglogowhite.webp")
    assert url.startswith("https://media.formula1.com/")


def test_logos_are_saved_once_per_season_and_team(conn, tmp_path):
    """Check each team of the season is saved as a PNG under its season, and kept after"""
    http = FakeHttp(FakeResponse(content=png_bytes()))
    report = team_logos.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None)
    assert report["saved"] == ["Haas F1 Team", "Team Alpha"]
    assert sorted(p.name for p in (tmp_path / "2026").glob("*.png")) == [
        "haasf1team.png", "teamalpha.png",
    ]
    again = team_logos.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None)
    assert again["kept"] == ["Haas F1 Team", "Team Alpha"] and len(http.urls) == 2


@pytest.mark.parametrize(
    "response",
    [FakeResponse(status=404), FakeResponse(content=b"<html>", content_type="text/html")],
)
def test_bad_answers_are_reported_and_not_saved(conn, tmp_path, response):
    """Check a missing logo or a page that is not an image saves nothing"""
    report = team_logos.download_all(conn, 2026, tmp_path, http=FakeHttp(response),
                                     sleep=lambda s: None)
    assert [team for team, _ in report["failed"]] == ["Haas F1 Team", "Team Alpha"]
    assert not list(tmp_path.rglob("*.png"))


def test_logo_path_refuses_odd_seasons_and_names(tmp_path):
    """Check a season or team name cannot point outside the logo folder"""
    with pytest.raises(ValueError):
        team_logos.logo_path(tmp_path, 26, "Team Alpha")
    assert team_logos.logo_path(tmp_path, 2026, "../../x").name == "x.png"


def test_review_sheet_shows_every_team(conn, tmp_path):
    """Check the review sheet is drawn with one tile per team"""
    http = FakeHttp(FakeResponse(content=png_bytes()))
    team_logos.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None)
    sheet = team_logos.review_sheet(conn, 2026, tmp_path)
    assert sheet.name == "review_sheet_2026.png"
    assert Image.open(sheet).size[1] >= team_logos.TILE_H


class FirstWordOnly(FakeHttp):
    """Finds a logo only under the first word of the team name"""

    def get(self, url, headers=None, timeout=None):
        """Answer 404 for full-name links and a logo for the short name"""
        self.urls.append(url)
        if "/haas/" in url:
            return FakeResponse(content=png_bytes())
        return FakeResponse(status=404)


def test_a_short_team_name_is_tried_when_the_full_name_is_not_found(conn, tmp_path):
    """Check the first word of the name is tried, and the logo is saved under the full name"""
    http = FirstWordOnly(None)
    report = team_logos.download_all(conn, 2026, tmp_path, http=http, sleep=lambda s: None)
    assert "Haas F1 Team" in report["saved"]
    assert (tmp_path / "2026" / "haasf1team.png").exists()
    assert any("/2026/haas/2026haaslogowhite" in url for url in http.urls)
