/*
 * SwitchBot Art Frame card: pick an image and show it on the frame.
 *
 * Served by the switchbot_art_frame integration, which also loads it into the
 * frontend, so it only needs adding to a dashboard:
 *
 *   type: custom:switchbot-art-frame-card
 *   entity: sensor.art_frame_eeff_local_images
 *   orientation: portrait   # or landscape
 *   size: 13.3              # or 7.3 or 31.5 (inches)
 *
 * The image is fitted to the frame in the browser and sent to the
 * show_image action as base64 (image_data), so nothing is stored on HA.
 */

const CARD_TAG = "switchbot-art-frame-card";
const MAX_IMAGES = 10;
// Screen resolution of each frame size (in inches), as [long side, short side].
// The frame doesn't report its size in a form we can map yet, so it's set in
// the card's config.
const SIZES = {
  "13.3": [1600, 1200],
  "7.3": [800, 480],
  "31.5": [2560, 1440],
};
const DEFAULT_SIZE = "13.3";
// JPEG qualities to try, best first, when the PNG is too large to send.
const JPEG_QUALITIES = [0.92, 0.85, 0.75, 0.65, 0.5, 0.4, 0.3];
// SwitchBot's upload endpoint rejects large bodies (413). Its limit isn't
// documented; the error comes from Fastify, whose default is 1 MiB, so stay
// well under that.
const MAX_DATA_CHARS = 900_000;
const HINT =
  '<span class="hint">Choose an image <small>(PNG or JPEG)</small></span>';

class SwitchBotArtFrameCard extends HTMLElement {
  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(
      (id) => id.startsWith("sensor.") && id.endsWith("_local_images"),
    );
    return { entity: entity || "" };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Set entity to the frame's Local images sensor");
    // YAML reads `size: 13.3` as a number, so compare it as a string.
    const size = String(config.size ?? DEFAULT_SIZE);
    if (!Object.hasOwn(SIZES, size)) {
      throw new Error(`size must be one of ${Object.keys(SIZES).join(", ")} (inches)`);
    }
    this._config = { orientation: "portrait", fit: "fill", ...config, size };
    this._fit = this._config.fit;
    this._busy = false;
    this._image = null;
    if (this._root) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._root) this._render();
    this._updateCount();
  }

  getCardSize() {
    return this._image ? 9 : 2;
  }

  get _size() {
    const [long, short] = SIZES[this._config.size];
    return this._config.orientation === "landscape" ? [long, short] : [short, long];
  }

  _render() {
    if (!this._root) this._root = this.attachShadow({ mode: "open" });
    const [w, h] = this._size;
    this._root.innerHTML = `
      <style>
        ha-card { padding: 16px; }
        .header { display: flex; flex-direction: column; gap: 2px; }
        .title { font-size: 1.2em; font-weight: 500; }
        .count { color: var(--secondary-text-color); font-size: 0.9em; }
        .count.full { color: var(--warning-color, #e6a700); }
        .pick {
          margin-top: 12px; width: 100%; min-height: 56px;
          border: 2px dashed var(--divider-color); border-radius: 12px;
          display: flex; align-items: center; justify-content: center;
          background: var(--secondary-background-color); cursor: pointer;
          overflow: hidden; position: relative; box-sizing: border-box;
          color: var(--secondary-text-color); text-align: center; padding: 12px;
          font: inherit;
        }
        .pick.has-image {
          aspect-ratio: ${w} / ${h}; max-height: 60vh; border-style: solid; padding: 0;
        }
        .pick canvas { width: 100%; height: 100%; display: block; }
        .pick[disabled] { cursor: progress; }
        .controls { display: none; gap: 8px; margin-top: 12px; align-items: center; }
        .controls.shown { display: flex; }
        .cancel {
          margin-left: auto; border: 0; background: none; padding: 10px 12px;
          color: var(--primary-color); font: inherit; cursor: pointer;
        }
        .fit { display: flex; border: 1px solid var(--divider-color); border-radius: 8px; overflow: hidden; }
        .fit button { border: 0; padding: 8px 12px; background: none; color: var(--primary-text-color); font: inherit; cursor: pointer; }
        .fit button.on { background: var(--primary-color); color: var(--text-primary-color); }
        .send {
          border: 0; border-radius: 8px; padding: 10px 18px;
          background: var(--primary-color); color: var(--text-primary-color);
          font: inherit; font-weight: 500; cursor: pointer;
        }
        .send[disabled], .fit button[disabled], .cancel[disabled] { opacity: 0.5; cursor: default; }
        .status { margin-top: 10px; min-height: 1.4em; font-size: 0.9em; color: var(--secondary-text-color); }
        .status.error { color: var(--error-color); }
        .status.ok { color: var(--success-color, var(--primary-color)); }
        input { display: none; }
      </style>
      <ha-card>
        <div class="header">
          <div class="title"></div>
          <div class="count"></div>
        </div>
        <button class="pick" type="button" aria-label="Choose an image">${HINT}</button>
        <input type="file" accept="image/png,image/jpeg">
        <div class="controls">
          <div class="fit" role="group" aria-label="How to fit the image">
            <button type="button" data-fit="fill" title="Crop to fill the frame">Fill</button>
            <button type="button" data-fit="fit" title="Show the whole image, with white borders">Fit</button>
          </div>
          <button class="cancel" type="button">Cancel</button>
          <button class="send" type="button" disabled>Show on frame</button>
        </div>
        <div class="status" role="status"></div>
      </ha-card>`;

    const $ = (s) => this._root.querySelector(s);
    // Config values are set as text so that values containing markup are not parsed as HTML.
    $(".title").textContent = this._config.title ?? "Art Frame";
    this._els = {
      count: $(".count"),
      pick: $(".pick"),
      input: $("input"),
      controls: $(".controls"),
      cancel: $(".cancel"),
      send: $(".send"),
      status: $(".status"),
      fitButtons: [...this._root.querySelectorAll(".fit button")],
    };
    this._els.pick.addEventListener("click", () => this._els.input.click());
    this._els.input.addEventListener("change", () => this._load(this._els.input.files[0]));
    this._els.send.addEventListener("click", () => this._send());
    this._els.cancel.addEventListener("click", () => {
      this._clear();
      this._status("");
    });
    for (const b of this._els.fitButtons) {
      b.addEventListener("click", () => {
        this._fit = b.dataset.fit;
        this._draw();
        this._sync();
      });
    }
    this._sync();
    this._updateCount();
  }

  _updateCount() {
    if (!this._els || !this._hass) return;
    const st = this._hass.states[this._config.entity];
    const el = this._els.count;
    const n = st ? Number.parseInt(st.state, 10) : Number.NaN;
    if (Number.isNaN(n)) {
      el.textContent = st ? "Image count unknown" : "Entity not found";
      el.classList.remove("full");
      return;
    }
    const full = n >= MAX_IMAGES;
    el.textContent = full
      ? `${n} of ${MAX_IMAGES} images \u00b7 sending replaces the oldest`
      : `${n} of ${MAX_IMAGES} images`;
    el.classList.toggle("full", full);
  }

  _sync() {
    const { pick, controls, cancel, send, fitButtons } = this._els;
    for (const b of fitButtons) {
      b.classList.toggle("on", b.dataset.fit === this._fit);
      b.disabled = this._busy;
    }
    pick.disabled = this._busy;
    // Idle, the card is just a "Choose an image" bar; the preview and its
    // controls appear once an image is picked.
    pick.classList.toggle("has-image", !!this._image);
    controls.classList.toggle("shown", !!this._image);
    cancel.disabled = this._busy;
    send.disabled = this._busy || !this._image;
    send.textContent = this._busy ? "Sending\u2026" : "Show on frame";
  }

  /** Drop the picked image and collapse back to the "Choose an image" bar. */
  _clear() {
    this._image = null;
    this._els.pick.innerHTML = HINT;
    this._sync();
  }

  _status(text, kind = "") {
    this._els.status.textContent = text;
    this._els.status.className = `status ${kind}`;
  }

  async _load(file) {
    if (!file) return;
    if (!["image/png", "image/jpeg"].includes(file.type)) {
      this._status("Choose a PNG or JPEG image", "error");
      return;
    }
    try {
      this._image = await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch (err) {
      this._status(`Couldn't read that image: ${err.message ?? err}`, "error");
      return;
    } finally {
      this._els.input.value = "";
    }
    this._status("");
    this._draw();
    this._sync();
  }

  /** Draw the image fitted to the frame's size; return the canvas. */
  _draw() {
    if (!this._image) return null;
    const [w, h] = this._size;
    let canvas = this._els.pick.querySelector("canvas");
    if (!canvas) {
      this._els.pick.replaceChildren();
      canvas = document.createElement("canvas");
      this._els.pick.append(canvas);
    }
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, w, h);
    const img = this._image;
    const scale =
      this._fit === "fill"
        ? Math.max(w / img.width, h / img.height)
        : Math.min(w / img.width, h / img.height);
    const dw = img.width * scale;
    const dh = img.height * scale;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(img, (w - dw) / 2, (h - dh) / 2, dw, dh);
    return canvas;
  }

  _encode(canvas) {
    // PNG keeps flat colours clean for the frame's dithering; fall back to
    // JPEG for photos that would make the message too large. The lowest
    // qualities are only reached on the largest frames.
    let data = canvas.toDataURL("image/png");
    for (const q of JPEG_QUALITIES) {
      if (data.length <= MAX_DATA_CHARS) break;
      data = canvas.toDataURL("image/jpeg", q);
    }
    if (data.length > MAX_DATA_CHARS) {
      throw new Error("The image is too large to send, even as a low-quality JPEG");
    }
    return data;
  }

  async _send() {
    if (this._busy || !this._image) return;
    this._busy = true;
    this._sync();
    this._status("Uploading and sending to the frame. This takes about a minute\u2026");
    try {
      const data = this._encode(this._draw());
      const reply = await this._hass.connection.sendMessagePromise({
        type: "call_service",
        domain: "switchbot_art_frame",
        service: "show_image",
        service_data: { image_data: data },
        target: { entity_id: this._config.entity },
        return_response: true,
      });
      const r = reply?.response?.[this._config.entity] ?? reply?.response ?? {};
      const replaced = r.deleted_image_id != null ? " (replaced the oldest image)" : "";
      this._status(`Done: it's on the frame${replaced}.`, "ok");
      this._busy = false;
      this._clear();
    } catch (err) {
      this._status(`Failed: ${err?.message ?? err?.code ?? err}`, "error");
    } finally {
      this._busy = false;
      this._sync();
    }
  }
}

// HA's app installs a replacement custom element registry when it starts
// (@webcomponents/scoped-custom-element-registry). This module can run before
// that (e.g. when served from cache), and anything defined in the original
// registry is then invisible to HA. So wait for HA's root element, which is
// defined in the new registry, before defining the card.
const defineCard = () => {
  if (!window.customElements.get(CARD_TAG)) {
    window.customElements.define(CARD_TAG, SwitchBotArtFrameCard);
  }
};

const whenAppReady = async () => {
  const deadline = Date.now() + 60_000;
  while (!window.customElements.get("home-assistant") && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
};

whenAppReady().then(defineCard);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === CARD_TAG)) {
  window.customCards.push({
    type: CARD_TAG,
    name: "SwitchBot Art Frame",
    description: "Choose an image and show it on the Art Frame.",
  });
}
