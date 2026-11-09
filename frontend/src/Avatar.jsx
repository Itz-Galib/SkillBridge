import { useEffect, useState } from "react";
import { api } from "./api.js";
import "./avatar.css";

let avatarCache = { key: "", url: "", promise: null };

function releaseCachedAvatar() {
  if (avatarCache.url) URL.revokeObjectURL(avatarCache.url);
  avatarCache = { key: "", url: "", promise: null };
}

export function useAvatar(hasAvatar, updated, ownerKey) {
  const [url, setUrl] = useState(null);

  useEffect(() => {
    let active = true;
    if (!hasAvatar || !updated) {
      if (avatarCache.key.startsWith(`${ownerKey}:`)) releaseCachedAvatar();
      setUrl(null);
      return () => { active = false; };
    }

    const key = `${ownerKey}:${updated}`;
    if (avatarCache.key !== key) {
      releaseCachedAvatar();
      avatarCache.key = key;
      avatarCache.promise = api("/candidate/avatar", { responseType: "blob" })
        .then((blob) => {
          const nextUrl = URL.createObjectURL(blob);
          if (avatarCache.key !== key) {
            URL.revokeObjectURL(nextUrl);
            return null;
          }
          avatarCache.url = nextUrl;
          avatarCache.promise = null;
          return nextUrl;
        })
        .catch((error) => {
          if (avatarCache.key === key) releaseCachedAvatar();
          throw error;
        });
    }

    const request = avatarCache.url
      ? Promise.resolve(avatarCache.url)
      : avatarCache.promise;
    request
      .then((nextUrl) => { if (active) setUrl(nextUrl); })
      .catch((error) => {
        if (active) {
          console.error("Could not load profile photo:", error);
          setUrl(null);
        }
      });
    return () => { active = false; };
  }, [hasAvatar, updated, ownerKey]);

  return url;
}

export default function Avatar({
  name = "Candidate",
  hasAvatar = false,
  updated = null,
  ownerKey = "",
  className = "",
  previewUrl = null,
}) {
  const url = useAvatar(hasAvatar, updated, ownerKey);
  const initials = String(name || "C")
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className={className}>
      {previewUrl || url ? (
        <img src={previewUrl || url} alt="" />
      ) : (
        <span className="avatar-fallback">{initials || "C"}</span>
      )}
    </div>
  );
}
