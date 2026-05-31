"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import {
  cameraLookup,
  personLookup,
  type Camera,
  type Person,
  type TelegramChannelOption,
} from "./types";

// Loads the reference data the rule builder needs (cameras, persons,
// telegram channels) and keeps the id->name lookups populated. Shared by
// the /rules/new and /rules/[id]/edit pages.
export function useRuleRefData() {
  const { authFetch } = useAuth();
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [telegramChannels, setTelegramChannels] = useState<TelegramChannelOption[]>([]);
  const [telegramChannelsLoading, setTelegramChannelsLoading] = useState(true);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setTelegramChannelsLoading(true);
    try {
      const [camRes, perRes, tgRes] = await Promise.all([
        authFetch("/api/cameras"),
        authFetch("/api/persons"),
        authFetch("/api/telegram/channels"),
      ]);
      if (camRes.ok) {
        const list: Camera[] = await camRes.json();
        setCameras(list);
        cameraLookup.clear();
        for (const c of list) cameraLookup.set(c.id, c.name);
      }
      if (perRes.ok) {
        const list: Person[] = await perRes.json();
        setPersons(list);
        personLookup.clear();
        for (const p of list) personLookup.set(p.id, p.display_name);
      }
      if (tgRes.ok) setTelegramChannels(await tgRes.json());
    } catch {
      /* silent */
    } finally {
      setLoading(false);
      setTelegramChannelsLoading(false);
    }
  }, [authFetch]);

  useEffect(() => {
    load();
  }, [load]);

  return { cameras, persons, telegramChannels, telegramChannelsLoading, loading };
}
