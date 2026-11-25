# Almanac MVP: deferred review findings

These are minor findings that per-task and final reviews deferred to the v1.0 plans. They are copied from the execution ledger. Line numbers refer to the code at the time each review ran.

- Task 3: minor (deferred): 4-backtick fence edge (cleaner.py:41,188); comment strip inside inline code spans (cleaner.py:203); untyped predicate (cleaner.py:150).
- Task 3: minor (deferred): inline code spans not protected from hidden-HTML strip; nested self-closing same-name tag over-consumes (fail-closed); '>' inside attribute value leaves stray text; unclosed-hidden-tag case untested.
- Task 4: minor (deferred): robots failure cached for run + logged as "robots" (want robots-unavailable, narrow except); bad Location retried; streamed size cap untested; conditional headers reused across cross-host redirects; FetchLog commits on shared conn (Task 13: give fetch log its own connection); INSERT OR REPLACE would corrupt FTS (Task 8: never use it / recursive_triggers); noai ignores UA prefix; NAT64/6to4 not unwrapped; ms includes waits; non-200 2xx fails.
- Task 5: minor (deferred): page cap counts attempts (web.py:84) — test can't distinguish; child sitemap count unbounded; _LINK_RE matches image links + truncates URLs with ')'; validators set at yield time (Task 13: save manifest only after store.put succeeds).
- Task 6: minor (deferred): unused fixtures in test_git_github_citation_url.
- Task 6: minor (deferred): scp-style git URL user@host:path not checked for userinfo (blocked anyway unless allow_private_network).
- Task 7: minor (deferred): hard max not guaranteed for pathological ≥3.6k-char heading / fence info string (chunker.py:221-231); dead code chunker.py:179-184; backticks in backtick info string; overlap-omitted test asserts only size; oversized-code test lacks tilde/4-backtick cases.
- Task 8: minor (deferred): ROLLBACK raising masks original error; NULL source_id silently; rollback test doesn't assert tech/source/isolation_level; readonly conn no busy_timeout.
- Task 9: minor (deferred): alias tokens split into drop set (react.js drops "js", c++ drops "c") — recall loss; version regex lacks trailing boundary ("react 2024"); Retriever mutates conn.row_factory; indexed_versions vs search disagree when all pages removed; add 3.9 vs 3.12 map_version test.
- Task 10: minor (deferred): fixed-point loop non-convergence unasserted; heading_path with _/* breaks italics; excerpt list relies on caller cap.
- Task 10: minor (deferred): test_cut_backs_off_before_open_fence brittle (±1 token budget) — rewrite with measured bounds; "``` …" closer not strict CommonMark; _open_fence ignores indented/long fences; "Cut: answer" when answer already 1 char.
- Task 11: minor (deferred): _write_log catches only OSError + clock outside guard; answer-level violation → E_INTERNAL not dropped; get_page loads manifest each call; engine imports chunker privates (move extract_section into chunker); dead try in list_technologies; weak nearby_topics test; symlink test skipped on this host.
- Task 11: minor (deferred): thread test KeyError message; barrier/join without timeout.
- Task 12: minor (deferred): type-level schema errors return SDK prose not E_BAD_INPUT envelope; schema lacks min/max bounds (could use Annotated Field); logs.configure runs before config check → creates logs/ for bad --dir; log-file test assertions loose.
- Task 13: minor (deferred): no add round-trip test on multi-tech commented file; set_block_style/indent churn; git init return code ignored; --yes/--name ignored silently; --json errors not JSON; FAILED line lacks next-step hint; import json inside conftest fn.
- Task 13: minor (deferred): os.replace PermissionError on Windows → raw traceback; fixed temp name almanac.yaml.tmp; message .tmp scrub string hack.
- Task 14: minor (deferred): last_status = last row (often a skipped 404/robots); reconfigure(errors=replace) also corrupts --json on cp1252 pipes; git local-path status untested; README should say --dir must be absolute for claude mcp add; weak error_count assertion.
- Task 14: minor (deferred): status on a pre-migration DB before any write-mode connect exits 3 (no such column).
- Final fix wave re-review: all 18 items ADDRESSED, no new Critical/Important. Minors (deferred): os.replace PermissionError if page open; empty GIT_CONFIG_GLOBAL drops user proxy/CA settings; stable_url ignores branch; README Windows example in bash fence; GIT_CONFIG_COUNT env injection.
