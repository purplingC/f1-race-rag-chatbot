# Sample data notice

This folder holds three races so the app can be tried without loading data: the 2024 Monaco Grand Prix, the 2025 British Grand Prix and the 2026 Bahrain Grand Prix (held in Malaysia). It is built from the full data with `python -m f1rag.sample`.

| File | Contents | Source and licence |
|---|---|---|
| `f1_stats.db` | Results, laps, stints, pit-lane times, weather and race-control messages | [OpenF1](https://openf1.org), [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) |
| `chroma/` | Passages from each race's Wikipedia article, with its URL and revision | [Wikipedia](https://en.wikipedia.org), [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |
| both | A short race summary written by Gemini from the two sources above | Derived from the sources above |

Wikipedia articles used, by Wikipedia contributors:

| Article | Revision |
|---|---|
| [2024 Monaco Grand Prix](https://en.wikipedia.org/wiki/2024_Monaco_Grand_Prix) | [1371788657](https://en.wikipedia.org/w/index.php?oldid=1371788657) |
| [2025 British Grand Prix](https://en.wikipedia.org/wiki/2025_British_Grand_Prix) | [1371643387](https://en.wikipedia.org/w/index.php?oldid=1371643387) |
| [2026 Bahrain Grand Prix in Malaysia](https://en.wikipedia.org/wiki/2026_Bahrain_Grand_Prix_in_Malaysia) | [1383725164](https://en.wikipedia.org/w/index.php?oldid=1383725164) |

The race data is derived from OpenF1 and is shared under the same licence, CC BY-NC-SA 4.0: non-commercial use only, with attribution, and any adaptation under the same terms. The Wikipedia passages keep their CC BY-SA 4.0 licence; each passage stores its article URL and revision for attribution.

This is an unofficial project, not associated with Formula 1, the FIA, any team or OpenF1. F1 and related marks belong to Formula One Licensing B.V. No photos or logos are included.
