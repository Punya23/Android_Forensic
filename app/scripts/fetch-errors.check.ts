// Runnable check for the failed-GET registry: `npm run check:errors`.
//
// Views swallow request failures into empty state; the registry is what lets the shell
// say "this empty panel may be an outage". Pins: failures are recorded, one row per path
// (a retry loop must not grow it), cleared on a later success, and dismissable.
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import {
  clearFetchFailure,
  dismissFetchFailures,
  recordFetchFailure,
  useFetchFailures,
} from "../src/lib/fetchErrors.ts";

/** Current registry contents, read through the real hook via a one-shot render. */
function rows(): string[] {
  let seen: string[] = [];
  function Probe() {
    seen = useFetchFailures().map((f) => `${f.path}|${f.message}`);
    return null;
  }
  renderToString(createElement(Probe));
  return seen;
}

dismissFetchFailures();
assert.deepEqual(rows(), []);

recordFetchFailure("/api/a", new Error("HTTP 500"));
recordFetchFailure("/api/b", "boom");
assert.deepEqual(rows(), ["/api/a|HTTP 500", "/api/b|boom"]);

// Same path again: replaced, not duplicated, newest message wins.
recordFetchFailure("/api/a", new Error("HTTP 502"));
assert.deepEqual(rows(), ["/api/b|boom", "/api/a|HTTP 502"]);

// A later success on the path clears it; clearing an unknown path is a no-op.
clearFetchFailure("/api/b");
clearFetchFailure("/api/never");
assert.deepEqual(rows(), ["/api/a|HTTP 502"]);

dismissFetchFailures();
assert.deepEqual(rows(), []);

console.log("fetch-errors check ok");
