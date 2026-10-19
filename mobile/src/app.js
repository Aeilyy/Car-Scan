import { Capacitor } from "@capacitor/core";
import { Camera } from "@capacitor/camera";
import { Directory, Filesystem } from "@capacitor/filesystem";
import { Preferences } from "@capacitor/preferences";
import { Share } from "@capacitor/share";

const NATIVE = Capacitor.isNativePlatform();
const $ = (s) => document.querySelector(s);
const FIELDS = ["owner_name", "vin", "license_plate"];

let serverBase = "";
let uploadId = null;
let scanRequest = null;

function show(id) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== id));
  window.scrollTo(0, 0);
}

let toastTimer;
function toast(text, ms = 2600) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms);
}

function normalizeServer(input) {
  let s = input.trim().replace(/\/+$/, "");
  if (!s) return "";
  if (!/^https?:\/\//i.test(s)) s = "http://" + s;
  const url = new URL(s);
  if (!url.port && url.protocol === "http:") url.port = "8000";
  return url.origin;
}

async function api(path, options = {}, base = serverBase) {
  let res;
  try {
    res = await fetch(base + path, options);
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new Error("Can't reach the server. Is NetBird connected and the laptop on?");
  }
  if (!res.ok) {
    let detail = `Server error ${res.status}.`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch { }
    throw new Error(detail);
  }
  return res;
}

async function shrinkPhoto(blob, maxEdge = 2000) {
  try {
    const bmp = await createImageBitmap(blob, { imageOrientation: "from-image" });
    const scale = Math.min(1, maxEdge / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    return (await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.9))) || blob;
  } catch {
    return blob;
  }
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1]);
    r.onerror = reject;
    r.readAsDataURL(blob);
  });
}

async function openSetup(canGoBack) {
  $("#server").value = serverBase.replace(/^http:\/\//, "");
  $("#setup-error").hidden = true;
  $("#btn-setup-back").hidden = !canGoBack;
  show("setup");
}

$("#setup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#btn-connect");
  const err = $("#setup-error");
  err.hidden = true;
  let base;
  try {
    base = normalizeServer($("#server").value);
  } catch {
    err.textContent = "That isn't a valid address. Example: 100.92.14.7:8000";
    err.hidden = false;
    return;
  }
  btn.disabled = true;
  btn.textContent = "Connecting…";
  try {
    const res = await api("/api/ping", { signal: AbortSignal.timeout(8000) }, base);
    if ((await res.json()).app !== "carscan") throw new Error("Something answered, but it isn't the CarScan server.");
    serverBase = base;
    await Preferences.set({ key: "server", value: base });
    goHome();
  } catch (ex) {
    err.textContent = ex.name === "TimeoutError" ? "No answer from that address. Check the IP and that NetBird is connected." : ex.message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Connect";
  }
});
$("#btn-setup-back").addEventListener("click", goHome);
$("#btn-server").addEventListener("click", () => openSetup(true));

function goHome() {
  show("home");
  refreshStatus();
}

async function refreshStatus() {
  const dot = $("#server-dot");
  if (NATIVE) {
    $("#btn-server").hidden = false;
    $("#server-label").textContent = serverBase.replace(/^http:\/\//, "");
    dot.className = "dot";
  }
  try {
    const { count } = await (await api("/api/stats", { signal: AbortSignal.timeout(8000) })).json();
    $("#count").textContent = count === 1 ? "1 entry" : `${count} entries`;
    dot.className = "dot ok";
  } catch {
    $("#count").textContent = NATIVE ? "Server offline" : "";
    dot.className = "dot down";
  }
}

$("#btn-scan").addEventListener("click", () => {
  if (NATIVE) $("#sheet").hidden = false;
  else $("#file").click();
});

$("#sheet").addEventListener("click", async (e) => {
  const source = e.target.dataset?.source || (e.target === $("#sheet") ? "cancel" : null);
  if (!source) return;
  $("#sheet").hidden = true;
  if (source === "cancel") return;
  try {
    const media =
      source === "camera"
        ? await Camera.takePhoto({ quality: 90, targetWidth: 2000, targetHeight: 2000, correctOrientation: true })
        : (await Camera.chooseFromGallery({ mediaType: 0, allowMultipleSelection: false, limit: 1 })).results[0];
    if (!media?.webPath) return;
    const blob = await (await fetch(media.webPath)).blob();
    startReview(blob);
  } catch (err) {
    const msg = String(err?.message || err);
    if (/cancel/i.test(msg)) return;
    if (/denied|permission/i.test(msg)) toast("Allow camera access for CarScan in the phone's Settings.", 4000);
    else toast("Couldn't get the photo: " + msg, 4000);
  }
});

$("#file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (file) startReview(file);
});

$("#btn-download").addEventListener("click", async () => {
  const btn = $("#btn-download");
  if (btn.disabled) return;
  btn.disabled = true;
  toast("Preparing Excel file…", 20000);
  try {
    const res = await api("/api/export");
    const blob = await res.blob();
    const name =
      /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "")?.[1] ||
      `vehicles_${new Date().toISOString().slice(0, 10)}.xlsx`;
    if (NATIVE) {
      const { uri } = await Filesystem.writeFile({ path: name, data: await blobToBase64(blob), directory: Directory.Cache });
      $("#toast").hidden = true;
      await Share.share({ title: name, files: [uri], dialogTitle: "Open or save the Excel file" });
    } else {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
      toast("Downloaded.");
    }
  } catch (err) {
    if (!/cancel/i.test(String(err?.message))) toast(err.message || "Download failed.", 4000);
    else $("#toast").hidden = true;
  } finally {
    btn.disabled = false;
  }
});

function resetReview() {
  uploadId = null;
  FIELDS.concat("description").forEach((k) => ($("#" + k).value = ""));
  document.querySelectorAll("[data-warn]").forEach((p) => { p.hidden = true; p.textContent = ""; });
  document.querySelectorAll(".field").forEach((f) => f.classList.remove("flagged"));
  $("#scan-status").textContent = "";
  $("#scan-status").className = "msg";
  $("#btn-send").disabled = true;
  $("#btn-send").textContent = "Send";
}

async function startReview(source) {
  scanRequest?.abort();
  resetReview();
  if (history.state?.view !== "review") history.pushState({ view: "review" }, "");
  show("review");

  const old = $("#preview").src;
  if (old.startsWith("blob:")) URL.revokeObjectURL(old);
  const photo = await shrinkPhoto(source);
  $("#preview").src = URL.createObjectURL(photo);

  const review = $("#review");
  $("#photo").classList.add("scanning");
  review.classList.add("loading");
  $("#scan-status").textContent = "Reading the document…";

  const form = new FormData();
  form.append("photo", photo, "paperwork.jpg");
  scanRequest = new AbortController();
  try {
    const res = await (await api("/api/scan", { method: "POST", body: form, signal: scanRequest.signal })).json();
    uploadId = res.upload_id;
    FIELDS.forEach((k) => ($("#" + k).value = res.fields[k] || ""));
    Object.entries(res.warnings || {}).forEach(([k, text]) => {
      const p = document.querySelector(`[data-warn="${k}"]`);
      if (!p) return;
      p.textContent = text;
      p.hidden = false;
      p.closest(".field").classList.add("flagged");
    });
    $("#scan-status").textContent = res.notice || "Check the fields, then add a description.";
    $("#btn-send").disabled = false;
  } catch (err) {
    if (err.name === "AbortError") return;
    $("#scan-status").className = "msg error";
    $("#scan-status").textContent = `Upload failed: ${err.message} Tap Retake to try again.`;
  } finally {
    $("#photo").classList.remove("scanning");
    review.classList.remove("loading");
  }
}

function leaveReview() {
  scanRequest?.abort();
  resetReview();
  goHome();
}

$("#btn-cancel").addEventListener("click", () => history.back());
$("#btn-retake").addEventListener("click", () => $("#btn-scan").click());
window.addEventListener("popstate", () => {
  if (!$("#review").hidden) leaveReview();
});

FIELDS.forEach((k) =>
  $("#" + k).addEventListener("input", () => {
    const p = document.querySelector(`[data-warn="${k}"]`);
    p.hidden = true;
    p.closest(".field").classList.remove("flagged");
  })
);

$("#btn-send").addEventListener("click", async () => {
  const btn = $("#btn-send");
  btn.disabled = true;
  btn.textContent = "Sending…";
  try {
    await api("/api/entries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        upload_id: uploadId,
        owner_name: $("#owner_name").value,
        vin: $("#vin").value,
        license_plate: $("#license_plate").value,
        description: $("#description").value,
      }),
    });
    history.back();
    toast("Sent. Saved to the Excel file.");
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Send";
    $("#scan-status").className = "msg error";
    $("#scan-status").textContent = `Not sent: ${err.message}`;
  }
});

(async () => {
  if (NATIVE) {
    serverBase = (await Preferences.get({ key: "server" })).value || "";
    if (!serverBase) return openSetup(false);
  } else if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
  goHome();
})();
