"use strict";

const DEFAULTS = {
  autoMode:       "background",
  autoThreshold:  100,
  playtimeNotify: 0,
};

async function load() {
  const s = await chrome.storage.local.get(["autoMode", "autoThreshold", "playtimeNotify"]);
  document.getElementById("sel-auto-mode").value  = s.autoMode       ?? DEFAULTS.autoMode;
  document.getElementById("sel-threshold").value  = s.autoThreshold  ?? DEFAULTS.autoThreshold;
  document.getElementById("sel-playtime").value   = s.playtimeNotify ?? DEFAULTS.playtimeNotify;
}

document.getElementById("btn-save").addEventListener("click", async () => {
  const settings = {
    autoMode:       document.getElementById("sel-auto-mode").value,
    autoThreshold:  parseInt(document.getElementById("sel-threshold").value),
    playtimeNotify: parseInt(document.getElementById("sel-playtime").value),
  };
  await chrome.storage.local.set(settings);

  // プレイ時間通知タイマーをリセット
  chrome.runtime.sendMessage({ type: "SETTINGS_UPDATED", settings });

  const msg = document.getElementById("saved-msg");
  msg.classList.add("show");
  setTimeout(() => msg.classList.remove("show"), 2000);
});

load();
