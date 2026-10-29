# Synthetic managed-login fixtures

These images are generated test data, not live screenshots. `box-selected.png`
and `box-captcha.png` draw the public zmBox 1.9 login layout in the existing
940×590 → 1147×720 coordinate space, with fictional account labels.
`save-role.png` adds the fictional name 角色甲 to the previously redacted
`bounty-candle/saves.png` fixture. No real account/password/profile is included.

Layout evidence: duskeye/zmBox commit 05e4975f61c5fe137552bd06ca97ee3cfa80279b,
`src/com/zmbox/view/LoginLayer.as` (ComboBox at 370,110, scale 2.5), and the
local public AIR SWF's 940×590 display rectangle. These checks exercise native
OCR/templates and Agent RPC only. They do not replace authorized real-client
acceptance of font, theme, rendering, DPI, account selection or login behavior.

`scroll/` contains full first-frame map artwork exported from the public current
town5/town6 SWFs. It contains no user screenshot or account information. The
generator `tools/research/import_scroll_landmarks.py` records SWF hashes, authored
map bounds and all clickable stage bounds in `stage-scroll.json`. Runtime crops
use Lanczos; full-map fixtures use bicubic resampling. The native test composites
the existing task button and normal-mode label over a moving memory viewport.
Full map fixtures are test-only; packages contain only small terrain templates.
