# Ctrip Auth Notes

- Browser capture used the logged-in Microsoft Edge context.
- CLI replay should use `ctrip-cli-sessions/ctrip_cookie_header.txt` first, falling back to `ctrip_auth_plain.json.cookieHeader`.
- Do not write Cookie values into committed request templates or docs.
- Required replay headers should match browser-like headers used by `im_archive_cli.ctrip_http.build_headers(...)`.
- If replay returns auth/permission errors, refresh login in Edge and regenerate `ctrip-cli-sessions` auth files before retrying.
