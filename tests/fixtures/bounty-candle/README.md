# Native candle recognition fixtures

Client screenshots use the target project's 720-pixel short-side coordinate space. These files are static calibration/test data; tests never open a game window or dispatch input. Character HUD identity is masked. The occupied save fixture replaces the role name/time with an anonymous `Lv.50` label; slot 2–4 fixtures retain their own slot digit and synthesize occupied content from that anonymous card.

Positive cases cover world/continent navigation, accepted cards in two positions, sunny/cloudy paused hubs, login, and each of the four selectable occupied save slots. Negative cases include an unaccepted bounty board, loading, paused battle, and the empty save slots. Additional wrong-card and moved-slot checks can be made from these frames in memory.

`candle-battle-ready` and `candle-hub-ready` are explicit synthetic branch fixtures: only the pause-text rectangle is masked. The cave, stairs and noticeboard recognition pixels are unchanged from their captured versions. These fixtures test that a missing pause prompt allows the positive ready/return branch, without pretending to be new live-game captures.

Tests must load only this directory and the target project's resources; the source project and retired migration backup are unnecessary.
