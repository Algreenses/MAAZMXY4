# Native candle recognition fixtures

Client screenshots use the target project's 720-pixel short-side coordinate space. These files are static calibration/test data; tests never open a game window or dispatch input. Character HUD identity is masked. The occupied save fixture replaces the role name/time with an anonymous `Lv.50` label; slot 2–4 fixtures retain their own slot digit and synthesize occupied content from that anonymous card.

The world/continent maps and old bounty-card images are retained as regression inputs for the retired route. The world map and old cards must not be treated as a ready quest screen; continent frames with the existing task icon may use that icon directly, without navigating through the old hub. Login, occupied save slots, loading and pause cases remain offline recognition coverage.

`candle-battle-ready` and `candle-hub-ready` are explicit synthetic branch fixtures: only the pause-text rectangle is masked. The cave, stairs and noticeboard recognition pixels are unchanged from their captured versions. These fixtures test that a missing pause prompt allows the positive ready/return branch, without pretending to be new live-game captures.

Tests must load only this directory and the target project's resources; the source project and retired migration backup are unnecessary.

`quest-map`, `quest-tab` and `quest-candle` are synthetic 1148×720 frames. They contain only a copy of the existing task icon and rendered labels in the normal quest-panel regions; they contain no captured player data. The native engine follows the three-click entry route against these frames and checks that recovery refuses a world-map state. They are not real-game acceptance evidence.
