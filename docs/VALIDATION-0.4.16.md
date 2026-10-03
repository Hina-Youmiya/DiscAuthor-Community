# Community 0.4.16 local validation

Tested on an Apple Silicon Mac running macOS 15.6. The company edition was kept running; tests used separate data directories and synthetic media.

| Check | Observed result |
| --- | --- |
| Focused public-edition and packaging tests | 14 passed |
| Packaged backend, restricted PATH | Started without external Python, Homebrew or codec tools |
| Synthetic export queue | Menu and single-video/no-menu ISO exports passed for both DVD and BD |
| Interface language / project text | Japanese programme names remained unchanged under ja, zh and en API requests |
| Feedback rendering | Selected and activated previews differed |
| Native app startup | Default and isolated preference domains both initialized, produced nine previews and exited normally with no backend left running |
| Native UI | Japanese default, separate workspaces, direct eject and close-tray buttons visible |
| Bundled player | Opened the exported DVD and BD menu ISOs; direction and confirm controls entered the synthetic video |
| BD menu focus | White text changed to yellow on the item selected with the down control |
| Portability scan | 255 regular files, 127 Mach-O images and 807 embedded archive entries; no scan errors or matching private build-path tokens |
| Bundle signature | Strict, deep ad-hoc signature verification passed |

The final frontend fixes a first-launch crash: a preferences suite matching the application's own bundle identifier can return nil. The app now uses its standard domain in that case and avoids forced unwrapping.

The tested backend SHA-256 is `bb166114398538b0dc337757b65bff022bc8174f3d64ee52848613d3aa0ed44b`. Native-only rebuilding did not change it. Upstream diagnostic build paths found in third-party binaries are recorded as scan warnings; they are not company resource paths or runtime dependencies.

These are limited software smoke tests. They do not certify every setting, long-duration encoding, burning, physical-drive operations, a clean second Mac, or standalone-player compatibility. No optical disc was burned during this validation. Panasonic DMP-BD65 compatibility remains unresolved. The source mapping and reproduction audit for all selected VLC plugins and bundled Python native components is not complete.
