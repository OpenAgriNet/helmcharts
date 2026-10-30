#!/bin/sh
# Digest every generated payload, so determinism can be proved without
# committing 130 MB of JSON.
#
#     tools/checksums.sh write     # record what the generators produce now
#     tools/checksums.sh verify    # regenerate-and-compare; non-zero if moved
#
# One line per file, sorted by path, which makes a mismatch point at the file
# that changed rather than just saying "different".
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
FILE="$HERE/data/CHECKSUMS"

# The generated sets. data/mandiPrice-metadata is NOT here: it is the input,
# fetched rather than generated, and it is committed in full.
DIRS="data/publish-payload data/discover-payload data/select-payload"

# sha256sum on Linux, shasum -a 256 on macOS. Neither is guaranteed, so say so
# rather than producing an empty file that verifies against anything.
# A command string rather than a shell function: xargs execs a program and
# cannot see a function defined here.
if command -v sha256sum >/dev/null 2>&1; then
  SHA="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
  SHA="shasum -a 256"
else
  echo "checksums: need sha256sum or shasum on PATH" >&2
  exit 1
fi

# One line per DIRECTORY, not per file: the file count and a digest of every
# file's digest, in sorted order.
#
# Per-file was 2.4 MB once the query count reached twenty thousand, which is not
# a thing to commit to a public repository for the sake of naming which file
# moved. Three lines catch the same drift; regenerating is how you then find
# which file it was.
digest() {
  cd "$HERE"
  for dir in $DIRS; do
    [ -d "$dir" ] || continue
    # -print0 and sort -z so a path with a space cannot split a line, and the
    # order is the same on every machine regardless of locale.
    summary="$(find "$dir" -type f -print0 | LC_ALL=C sort -z | xargs -0 -r $SHA)"
    count="$(printf '%s\n' "$summary" | grep -c . || true)"
    rolled="$(printf '%s' "$summary" | $SHA | cut -d' ' -f1)"
    printf '%s  %s files  %s\n' "$rolled" "$count" "$dir"
  done
}

case "${1:-}" in
  write)
    digest > "$FILE"
    echo "checksums: wrote data/CHECKSUMS"
    cat "$FILE"
    ;;
  verify)
    if [ ! -f "$FILE" ]; then
      echo "checksums: data/CHECKSUMS is missing -- run 'make checksums' to record one" >&2
      exit 1
    fi
    actual="$(digest)"
    expected="$(cat "$FILE")"
    if [ "$actual" = "$expected" ]; then
      echo "verify-data: matches data/CHECKSUMS"
      printf '%s\n' "$actual" | sed 's/^/    /' 
    else
      echo "verify-data: the generators produce something different from data/CHECKSUMS." >&2
      echo "" >&2
      printf '%s\n' "$expected" > "$HERE/.checksums.expected"
      printf '%s\n' "$actual" > "$HERE/.checksums.actual"
      diff "$HERE/.checksums.expected" "$HERE/.checksums.actual" | head -20 >&2 || true
      rm -f "$HERE/.checksums.expected" "$HERE/.checksums.actual"
      echo "" >&2
      echo "If a config, template or the metadata changed on purpose, re-record with:" >&2
      echo "    make checksums" >&2
      exit 1
    fi
    ;;
  *)
    echo "usage: $0 write|verify" >&2
    exit 2
    ;;
esac
