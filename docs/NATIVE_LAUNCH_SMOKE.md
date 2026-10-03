# Native application launch smoke test

Run this after packaging, in a logged-in macOS desktop session:

```sh
python3 scripts/smoke_native_launch.py \
  --app '/path/to/DiscAuthor Community.app' \
  --work-dir '/tmp/discauthor-native-launch-smoke'
```

The work directory must not already exist. Two temporary windows open in turn:
first with the application's real default preference domain, then with an
explicit unique preference suite. This checks the shipped Swift executable,
including preference initialization before the backend starts. It does not
copy the preference-selection implementation into a synthetic test.

Each run has an empty, separate application-data directory. The test verifies
an actual child backend's health, the font catalog and menu-preview files
created by native startup, and continued process survival for five seconds.
It requests a normal AppKit quit by exact PID and requires both native and
backend processes to exit. Existing app instances are not targeted.

Results and logs remain in the work directory. Any crash, missing initialization,
forced shutdown or leftover backend fails the test. Development runtime
overrides are removed. The script does not change language/mode settings or
write preference values; the default case reads the Community application's
normal preference domain. Backend data is isolated in both cases.

No export, burn, erase, eject or tray API is called. This is startup regression
evidence, not a full visual review, proof of every preference read/write path,
or physical-disc playback certification. Use `--preferences-mode default` or
`override` to run one case when diagnosing a failure.
