/**
 * Timeline-level observation coalescer.
 *
 * Reality. When the same person enters a room three times in ten
 * minutes, the perception pipeline produces three separate
 * Observation rows. The dashboard timeline used to render three
 * cards. This module collapses them into one rolling card with a
 * count + per-occurrence strip.
 *
 * Pure function, no IO. Backed by a sliding-window walk so the
 * grouping is stable as new observations stream in.
 */

import type { Observation, FaceDetection, Detection } from "./observation-grouping-types";

export interface ObservationGroup {
  __group: true;
  key: string;
  // Newest first inside the group so the headline timestamp matches
  // the latest occurrence.
  observations: Observation[];
  // Stable id for React key. Derived from the newest observation's id
  // so the same group keeps the same key as new occurrences arrive.
  id: string;
  camera_id: string;
  // Latest observation drives the visible thumbnail + description.
  latest: Observation;
  occurrences: number;
}

export type CoalesceEntry = Observation | ObservationGroup;

export function isObservationGroup(e: CoalesceEntry): e is ObservationGroup {
  return (e as ObservationGroup).__group === true;
}

/**
 * Build the group key for an observation. Two observations land in the
 * same group iff they have the same key AND are within ``windowMs`` of
 * each other AND are on the same camera.
 *
 * Priority. Named persons > unknown faces > top objects > motion.
 * License plates are excluded from the object signature because they
 * are surfaced separately in the UI.
 */
export function groupKey(o: Observation): string {
  const cam = o.camera_id;
  const faces: FaceDetection[] = o.person_detections?.faces || [];
  const named = Array.from(
    new Set(
      faces
        .map((f) => f.person_name)
        .filter((n): n is string => !!n)
    )
  ).sort();
  if (named.length > 0) {
    return `${cam}|p:${named.join(",")}`;
  }
  if (faces.length > 0) {
    // Unknown faces. Cluster ids let us tell "the same stranger keeps
    // coming back" apart from "different strangers each time" when
    // available.
    const clusters = Array.from(
      new Set(
        faces
          .map((f) => f.cluster_id)
          .filter((c): c is string => !!c)
      )
    ).sort();
    if (clusters.length > 0) {
      return `${cam}|c:${clusters.join(",")}`;
    }
    return `${cam}|unknown`;
  }
  const objs: Detection[] = (o.object_detections?.objects || []).filter(
    (d) => d.label !== "license_plate"
  );
  if (objs.length > 0) {
    const top = Array.from(new Set(objs.map((d) => d.label))).sort().slice(0, 3);
    return `${cam}|o:${top.join(",")}`;
  }
  return `${cam}|motion`;
}

/**
 * Coalesce observations into rolling groups bounded by ``windowMs``.
 * Walks newest-first because that is the order the dashboard renders.
 * A group only stays open while the gap between successive
 * observations stays under the window; once the gap exceeds the
 * window a fresh group opens.
 *
 * Pass ``windowMs <= 0`` to disable grouping. The function then
 * returns the input untouched so callers can flip the feature off
 * without restructuring their render path.
 */
export function coalesceObservations(
  observations: Observation[],
  windowMs: number
): CoalesceEntry[] {
  if (windowMs <= 0 || observations.length <= 1) {
    return [...observations];
  }
  // Sort newest-first. Stable on equal timestamps.
  const sorted = [...observations].sort(
    (a, b) =>
      new Date(b.started_at).getTime() - new Date(a.started_at).getTime()
  );

  // Open buckets keyed by groupKey. Each bucket tracks the
  // observations collected so far (newest -> oldest) and the
  // timestamp of the OLDEST member, which is the boundary for
  // accepting another older observation.
  interface OpenBucket {
    key: string;
    items: Observation[];
    oldestTs: number;
  }
  const open = new Map<string, OpenBucket>();
  // Each entry as we finalize it. We finalize in walk order so the
  // resulting list is also newest-first by group-anchor timestamp
  // (each group anchors at its newest member).
  const out: CoalesceEntry[] = [];

  for (const o of sorted) {
    const key = groupKey(o);
    const ts = new Date(o.started_at).getTime();
    const bucket = open.get(key);
    if (bucket && bucket.oldestTs - ts <= windowMs) {
      // o is within the window of the bucket's oldest member. Append.
      bucket.items.push(o);
      bucket.oldestTs = ts;
      continue;
    }
    // Either no bucket yet for this key or the gap is too large.
    // Finalize any existing bucket for the key, then open a new one.
    if (bucket) {
      out.push(bucketToEntry(bucket));
    }
    open.set(key, { key, items: [o], oldestTs: ts });
  }
  // Flush remaining open buckets in newest-first order. Iteration
  // order of Map is insertion order; the buckets we open first have
  // the newest anchors so the flush order matches the desired output.
  // We also need to interleave each bucket's anchor at its position
  // among the singles. Because groups always anchor at their newest
  // member, and we walk newest-first, the order we finalize matches
  // chronological-newest-first as long as each bucket's newest member
  // was first encountered before we started a new bucket with the
  // same key.
  for (const bucket of open.values()) {
    out.push(bucketToEntry(bucket));
  }

  // Final sort by anchor (newest member) timestamp so the user
  // perceives strict time order regardless of group/single mix.
  out.sort((a, b) => anchorTs(b) - anchorTs(a));
  return out;
}

function bucketToEntry(b: { key: string; items: Observation[] }): CoalesceEntry {
  if (b.items.length === 1) return b.items[0];
  // items already collected newest -> oldest by walk order.
  const latest = b.items[0];
  return {
    __group: true,
    key: b.key,
    observations: b.items,
    id: `group-${latest.id}`,
    camera_id: latest.camera_id,
    latest,
    occurrences: b.items.length,
  };
}

function anchorTs(e: CoalesceEntry): number {
  if (isObservationGroup(e)) {
    return new Date(e.latest.started_at).getTime();
  }
  return new Date(e.started_at).getTime();
}
