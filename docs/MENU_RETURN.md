# Menu return fix — 0.4.17

This update adds missing authored return entries. An application update does not change existing ISO files or burned discs; menu-enabled discs need a new export to receive these entries.

## DVD

The previous DVD authoring XML created a VTS Root menu but no VMGM Title menu entry. An independent libdvdnav test of a previous export reproduced `DVD_MENU_Title` failing with "No such menu or menu not reachable", while Root and Escape could return. Testing only entry from a menu into a movie, or VLC's Root-menu shortcut, missed this defect.

Menu-enabled exports now include a VMGM `entry="title"` PGC that forwards to the VTS Root entry. Saved menu page and button registers are preserved, so both Title/Top Menu and Root/Menu return to the originating programme page and selection. A single-video no-menu disc retains its direct-play, stop-at-end route.

## Blu-ray

The inspected previous Blu-ray had a reachable Top Menu and unmasked menu-call operations. Calling `bd_menu_call()` during real title-stream reads returned successfully; the movie contained no Popup Menu IG stream, so `BD_VK_POPUP` failed.

Each menu-enabled movie now contains a hidden popup composition. Opening it runs a default auto-action button with `JumpTitle(0)`. The indexed Top Menu dispatches to the saved menu page, with the programme selection retained. Its transparent graphics do not draw over the movie. Popup segments start at PTS zero to make the entry available earlier; they are multiplexed into the movie with the correct frame-rate code. Only the actual menu playlists receive infinite still mode.

Popup control becomes available after the player decodes the initial IG data. The integration check waits for the player's availability event before exercising normal playback and seeking. Seeking past the initial graphics before initialization is complete is not certified by this change. Plain RETURN buttons are mapped by individual players; HDMV Top Menu and Popup are distinct commands.

## Validation

The updated local application exported four synthetic cases: menu and single-video/no-menu for both DVD and Blu-ray. The menu fixtures contain four programmes over two pages. Software navigation checks use VideoLAN's independent engines, not the editor's preview:

- DVD: all four titles returned through Title, Root and Escape, giving 12 successful return/replay combinations. Each returned to the correct page/button and replayed the expected title. The no-menu export reached stop after its only video.
- Blu-ray: all four titles returned through Top Menu, Popup and natural title completion, giving 12 successful return/replay combinations. Seeking after popup initialization was included. The no-menu export reached EOF and remained stopped.
- The old DVD without a Title entry and the old BD without movie popup IG serve as negative controls: the relevant checks fail for those images.
- Binary tests distinguish always-on menu IG from popup IG, check dispatcher routing, and verify transparent auto-action objects. Generic menu and public-edition tests remain in place.

Run the ordinary source tests:

```sh
python -m unittest discover -s tests -p 'test_*.py'
```

The DVD integration tests additionally require libdvdnav and real authoring tools. Set `DISCAUTHOR_TEST_LIBDVDNAV`, `DISCAUTHOR_TEST_FFMPEG`, `DISCAUTHOR_TEST_DVDAUTHOR` and `DISCAUTHOR_TEST_SPUMUX`, then run `test_dvd_menu_navigation.py` through unittest.

For a generated Blu-ray, save a JSON map containing `pages` from `author.pages_for(project)`, `remember_selection` and `first_button`, then run:

```sh
python scripts/check_menu_navigation.py \
  --iso sample.iso --library /path/to/libbluray.2.dylib \
  --menu-map menu-map.json --report navigation.json

python scripts/check_menu_navigation.py \
  --iso single-video.iso --library /path/to/libbluray.2.dylib \
  --no-menu --report stop.json
```

These are software navigation and structure checks. They do not certify every physical player or plain RETURN-button mapping. No blank media was burned during this repair. Panasonic DMP-BD65 recognition remains a separate unresolved issue; the change does not claim to fix it.

Primary references: [VideoLAN menu-call API](https://videolan.videolan.me/libbluray/bluray_8h.html), [HDMV keys](https://videolan.videolan.me/libbluray/keys_8h_source.html), [VLC's DVD navigation implementation](https://github.com/videolan/vlc/blob/3.0.x/modules/access/dvdnav.c).
