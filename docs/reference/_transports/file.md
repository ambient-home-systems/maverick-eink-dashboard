Suits anything that reads a file rather than a network request: a
jailbroken Kindle whose screensaver client rsyncs a directory, a Samba
share a panel-driving script mounts, a separate web server serving the
directory itself, or just watching the output on disk while tuning a theme.

This is a push transport: "delivered" means the file has been written (and
atomically renamed into place) before `deliver()` returns, not that
anything downstream has picked it up yet — the transport has no way to know
whether or when that happens.

Failures this transport can return, and what to do about each:

| Message | Cause | Fix |
| --- | --- | --- |
| `could not write <directory>: <exc>` | The target directory could not be created, or the frame file could not be written or renamed into place (permissions, a full disk, a path that is actually a file). | Check `transport.path` is a directory this process can create and write to, and that there is disk space free. |
