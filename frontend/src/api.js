const KEY = "sb_token";
// Set VITE_API_BASE_URL in Vercel to the deployed FastAPI origin (for example, https://skillbridge.onrender.com).
const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").trim().replace(/\/+$/, "");
export const getToken = () => localStorage.getItem(KEY);
export const setToken = (t) => (t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY));

function formatApiError(detail, statusText) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const path = Array.isArray(item?.loc) ? item.loc.join(".") : "";
        return [path, item?.msg].filter(Boolean).join(": ");
      })
      .filter(Boolean)
      .join("; ") || statusText;
  }
  if (detail && typeof detail === "object") {
    const messages = [];
    if (typeof detail.message === "string") messages.push(detail.message);
    if (Array.isArray(detail.fields)) {
      messages.push(
        detail.fields
          .map((item) => `${item.field}: missing ${(item.missing || []).join(", ")}`)
          .join("; "),
      );
    }
    if (Array.isArray(detail.errors)) {
      messages.push(
        detail.errors
          .map((item) => {
            const path = Array.isArray(item?.loc) ? item.loc.join(".") : "";
            return [path, item?.msg].filter(Boolean).join(": ");
          })
          .filter(Boolean)
          .join("; "),
      );
    }
    return messages.filter(Boolean).join(" ") || statusText;
  }
  return statusText;
}

export async function api(path, { method = "GET", body, form, responseType } = {}) {
  const headers = {};
  const t = getToken();
  if (t) headers.Authorization = "Bearer " + t;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(API_BASE + "/api" + path, { method, headers, body: payload });
  let data = null;
  if (res.ok && responseType === "blob") return res.blob();
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) {
    const err = new Error(formatApiError(data?.detail, res.statusText));
    err.status = res.status; err.code = typeof data?.detail === "string" ? data.detail : null;
    throw err;
  }
  return data;
}
