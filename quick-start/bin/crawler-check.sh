#!/usr/bin/env bash
#
# Is the network adapter's catalog crawler ticking, and is it publishing?
# (README, Appendix O.)
#
#     bin/crawler-check.sh          last run, and the crawler's log for the last hour
#     bin/crawler-check.sh --rerun  forget the last run: the next tick runs the pipeline
#     bin/crawler-check.sh --watch  follow the crawler's log (Ctrl-C to stop)
#
# Every tick (catalogIntervalSeconds) logs "publish pipeline did not run here"
# with the reason (not due) at debug level. A run logs "publish pipeline ran"
# with catalogs= and published=; a failure logs "publish pipeline run failed".
#
# The schedule is daily, so after the first run nothing runs until tomorrow.
# --rerun clears the run log, which is what decides that.

set -euo pipefail

runlog() {
  docker exec crawler-postgres psql -U crawler -d catalogcrawler -tAc "$1"
}

crawler_log() {
  docker logs "$@" network-adapter 2>&1 | grep --line-buffered 'catalogcrawler'
}

case "${1:-}" in
  --rerun)
    runlog "DELETE FROM crawler_pipeline_run"
    echo "run log cleared: the next tick runs the pipeline"
    exit 0
    ;;
  --watch)
    crawler_log -f --since 1m
    exit 0
    ;;
  "") ;;
  *)
    echo "usage: $0 [--rerun | --watch]" >&2
    exit 2
    ;;
esac

echo "== last run per pipeline"
runlog "SELECT pipeline, last_run_at FROM crawler_pipeline_run" || true
echo
echo "== ticks in the last hour"
crawler_log --since 1h | grep -c 'did not run here' || true
echo
echo "== runs in the last hour"
crawler_log --since 1h | grep -E 'publish pipeline (ran|run failed)|broken|giving up' || echo "(none)"
