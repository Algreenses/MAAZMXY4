# Navigation pixels

These are static client images in the 720-pixel coordinate space, never screenshots taken by a test. The source is the user-owned ZaoMengScript capture library; crop/frame hashes and review masks are recorded in `agent/data/navigation/` and `tools/navigation_privacy_masks.json`. Identifying nickname/announcement/save-time areas have been obscured. The game artwork belongs to 4399 and the respective rights holders.

`challenge-failed.png` and `challenge-succeeded.png` come from the two user-provided outcome images. The Windows border was removed and client pixels rescaled to 1147×720. Runtime templates are exact crops of these normalized images, rather than generated artwork.

`quest-taizong-real.png` is the existing screenshot `debug/on_error/2026.10.27-15.14.45.108_悬赏（守卫太宗）-刷取次数.png` in the user-provided `log_20261027_151703-part02.zip`. `task-map-real.png` comes from `debug/on_error/2026.10.27-15.13.06.874_悬赏（守卫太宗）-点击任务.png` in the same archive. These exercise the already-open accepted quest and missed-click regression. The candle memory-controller test copies the real panel header onto its explicitly synthetic quest-tab frame; this is a composite fixture, not live acceptance evidence.

The native tests use only a CustomController backed by these pixels. Any unplanned device action is forbidden; the test does not load a Win32 controller or open/focus a game window.
