# SwitchBot Art Frame Tools

> [!IMPORTANT]
> This is not an official SwitchBot integration and isn't affiliated with or
> supported by SwitchBot. It relies on reverse-engineered Bluetooth commands
> and SwitchBot's undocumented app API, so a firmware update for the frame or
> a change to SwitchBot's servers could stop some or all of it working.

A Home Assistant custom integration that adds image management to a
first-generation SwitchBot AI Art Frame already set up in the core SwitchBot
Bluetooth integration, so
the images stored on the frame can be managed from Home Assistant rather than
the SwitchBot app. It adds a sensor listing the stored images, actions to
delete an image and to put a new image on the frame, and a dashboard card for
sending an image from the browser. It doesn't connect to the frame or hold its
encryption key itself: every Bluetooth command goes through the device the
core integration already manages.

## Supported frames

The first-generation SwitchBot AI Art Frame, which comes in three sizes:

| Size  | Resolution | Status                                                |
|-------|:----------:|-------------------------------------------------------|
| 13.3" | 1200×1600  | Tested                                                |
| 7.3"  |  800×480   | Untested; the actions should work, but see below      |
| 31.5" | 2560×1440  | Untested; the actions should work, but see below      |

The examples in this README size images for the 13.3" frame (1200×1600). On
the other sizes, scale images to that frame's resolution yourself; how a frame
shows an image of the wrong size hasn't been tested. The dashboard card sizes
images for any of the three once its `size` option is set (see
[Dashboard card](#dashboard-card)), though only the 13.3" has been tried.

The **AI Art Frame 2** (released October 2026) is not supported and probably
won't work. It may not be recognised by the core SwitchBot integration yet,
and it may work very differently: different commands, screen resolutions and
image storage. Reports from owners are welcome in the
[issues](https://github.com/se0siris/ha-switchbot-art-frame/issues).

## Requirements

- **Home Assistant 2026.10.1** (PySwitchbot 2.9.0). This is the version it is
  built and tested against; it uses internals of other integrations that may
  change in later releases (see [Caveats](#caveats)).
- **SwitchBot Bluetooth** (the core `switchbot` integration), with the Art
  Frame already set up in it. This is required for everything: this
  integration reuses that entry's device, encryption key and Bluetooth routing.
- **Bluetooth in range of the frame.** Home Assistant talks to the frame
  itself only over Bluetooth (uploads and the cloud album go over the
  internet), so the frame must be in range of a Bluetooth adapter on the Home
  Assistant machine or of a Bluetooth proxy. If the frame is too far from Home
  Assistant, an [ESPHome Bluetooth proxy](https://esphome.io/projects/?type=bluetooth)
  placed near it extends the range; Home Assistant routes commands through
  whichever adapter or proxy can reach the frame. The proxy must allow active
  connections (`active` under `bluetooth_proxy`). That is the default on ESP32
  and RP2040 boards; BK72xx and LN882x boards only relay advertisements, so
  they can't be used to send commands to the frame.
- **SwitchBot Cloud** (the core `switchbot_cloud` integration), set up with
  your SwitchBot API token and secret. Needed for `show_image`, `upload_image` and the
  dashboard card, because new images are uploaded through it.
- **A SwitchBot account** with an email and password (accounts that sign in
  with Google or Apple won't work), added to this integration. Needed for
  `show_image` and the dashboard card, and to remove deleted images from
  SwitchBot's cloud album. See [SwitchBot account](#switchbot-account-optional).

| Feature                     | SwitchBot Bluetooth | SwitchBot Cloud | Account  |
|-----------------------------|:-------------------:|:---------------:|:--------:|
| Local images sensor         |         yes         |                 |          |
| `delete_image`              |         yes         |                 | optional |
| `download_image`            |         yes         |                 |          |
| `upload_image`              |         yes         |       yes       |          |
| `show_image`, card          |         yes         |       yes       |   yes    |

## Install

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=se0siris&repository=ha-switchbot-art-frame&category=integration)

With [HACS](https://hacs.xyz):

1. In HACS, open the ⋮ menu → **Custom repositories**, add
   `https://github.com/se0siris/ha-switchbot-art-frame` with the type
   **Integration**. (The button above does this for you.)
2. Find **SwitchBot Art Frame Tools** in HACS and download it.
3. Restart Home Assistant.

Or by hand: copy `custom_components/switchbot_art_frame` into your Home
Assistant config's `custom_components` folder, then restart Home Assistant.

Then go to Settings → Devices & services → Add integration → **SwitchBot Art
Frame Tools**, and pick the frame.

The integration adds one entity to the existing Art Frame device.

### Local images sensor

The sensor's state is the number of images stored on the frame (at most 10).
Its attributes are:

- `image_ids`: the stored images' IDs, oldest first;
- `current_image_id`: the image on display;
- `free_slots`: how many more images the frame can hold.

The sensor updates when the frame advertises over Bluetooth and after each
action. `homeassistant.update_entity` on the sensor reads the frame's status
straight away.

### SwitchBot account (optional)

Add your SwitchBot account's email and password while setting up, or later
with **Reconfigure** on the integration's entry. Reconfigure also changes or
removes the account.

The password is used only to log in and isn't stored. Logging in returns
session tokens, which are stored in Home Assistant's config like other
integrations' tokens and renewed automatically (SwitchBot's access tokens last
two hours, the refresh token 30 days). If SwitchBot ends the session, Home
Assistant shows a **Re-authenticate** prompt under Settings → Devices &
services asking for the password again. Until then `show_image` fails and
`delete_image` deletes only from the frame. Updating from version 0.5 or
earlier swaps a stored password for tokens the first time the integration
starts and can reach SwitchBot. If SwitchBot refuses the password, it is
removed and Home Assistant asks for it again.

The account is used with SwitchBot's private app API (the one PySwitchbot uses
to fetch encryption keys), found by reading the SwitchBot app's Art Frame
module. SwitchBot doesn't support third-party use of it, it could change
without notice, and using it may be against SwitchBot's terms.

## Actions

Every action targets the frame's Local images sensor. Each returns a response
when called with `response_variable` in a script or automation, or with
**Return response** in Developer tools → Actions.

If the frame is unavailable in Home Assistant (no recent Bluetooth
advertisements), Home Assistant skips the action without an error. A response
that doesn't include the sensor's entity ID means the action didn't run.

### show_image

Puts a new image on the frame and shows it. Needs the SwitchBot Cloud
integration and a SwitchBot account.

Fields (give exactly one of the first three):

- `image_url`: a web address SwitchBot's servers can reach;
- `image_path`: a PNG or JPEG on the Home Assistant machine, in a folder Home
  Assistant is allowed to read (such as `/config/www` or `/media`; see
  `allowlist_external_dirs`);
- `image_data`: a PNG or JPEG, base64-encoded or as a `data:` URI;
- `make_room` (default `true`): if the frame is full, delete its oldest image
  first, and if SwitchBot's cloud album is full of uploads the frame never
  downloaded, remove the oldest of them. With `false`, the action fails
  instead.
- `show` (default `true`): display the image once it has downloaded. With
  `false` it is only stored on the frame, for example to fill the frame for
  its own slideshow without flipping through every new image.

Files and data are sent to SwitchBot inline, so no public URL is needed.
SwitchBot rejects uploads larger than about 1 MiB, so a file or data must be
under about 650 KB (900,000 base64 characters); a 1200×1600 JPEG usually is.
A larger image is rejected before anything on the frame changes.

The action:

1. removes entries from the frame's cloud album for images no longer on the
   frame (left over from deletes made outside the SwitchBot app), so they
   don't count against the cloud's 10-image limit;
2. if `make_room` is `true`, makes room: if uploads the frame never
   downloaded (left by an earlier `show_image` whose download failed, or by
   `upload_image`) would fill the cloud's 10 slots, removes the oldest of
   them, and if the frame is full, deletes its oldest image, on the frame and
   in the cloud;
3. uploads the image through the SwitchBot Cloud integration (the same call
   `switchbot_cloud.upload_art_frame_image` makes);
4. finds the ID SwitchBot assigned to the new image in the cloud album;
5. tells the frame over Bluetooth to download that image, which it fetches
   over its own Wi-Fi (about 25 seconds), and shows it (unless `show` is
   `false`). A download that fails
   quickly is retried once, as for `download_image`.

Response:

- `image_id`: the new image's ID;
- `image_ids`, `current_image_id`: the frame's images and the one on display
  afterwards;
- `progress`: the download percentages the frame reported;
- `deleted_image_id`: the image deleted to make room, or `null`;
- `removed_cloud_leftovers`: the IDs of the cloud album entries removed in
  step 1;
- `removed_pending_uploads`: the IDs of the never-downloaded uploads removed
  in step 2.

```yaml
action: switchbot_art_frame.show_image
target:
  entity_id: sensor.art_frame_eeff_local_images
data:
  image_path: /config/www/frame/today.png
```

> [!TIP]
> SwitchBot's servers fail to fetch some image URLs, such as ones that
> redirect (Wikimedia Commons' `Special:FilePath` links, for example) or very
> large images. Passing the URL through an image proxy such as
> [wsrv.nl](https://wsrv.nl/) (a free, open-source third-party service) can
> help: it fetches the image and returns a resized JPEG from a plain URL. The
> original URL must be URL-encoded:
>
> ```yaml
> action: switchbot_art_frame.show_image
> target:
>   entity_id: sensor.art_frame_eeff_local_images
> data:
>   image_url: "https://wsrv.nl/?url=example.com%2Fphotos%2Fbeach.jpg%3Fsize%3Dlarge&w=1200&h=1600&fit=contain&cbg=white&output=jpg"
> ```
>
> `fit=contain` shows the whole image with `cbg` as the border colour;
> `fit=cover` crops it to fill the frame instead. Use `w=1600&h=1200` for a
> landscape frame.

### delete_image

Deletes one image stored on the frame, over Bluetooth. With a SwitchBot
account, it also removes the image from the frame's cloud album, as the
SwitchBot app does. `show_image` makes room by itself, so this is mainly for
removing a particular image.

Fields:

- `image` (required): one of
  - an image ID from the sensor's `image_ids` attribute (for example `16`);
  - `current`: the image on display;
  - `oldest` or `newest`. The frame lists its images oldest first, so `first`
    and `last` mean the same. (The SwitchBot app lists them the other way
    round.)

If the image is the one on display and the frame holds others, the frame is
switched to the next image first, so it is never left showing a deleted
image. The action reads the frame's status before and after, and fails if the
image is still listed afterwards. The cloud album delete is best effort: if it
fails, the image is still deleted from the frame.

Response:

- `deleted_image_id`: the image deleted;
- `switched_to_image_id`: the image switched to first, or `null`;
- `remaining_image_ids`: the images left on the frame;
- `cloud_deleted`: `true` or `false`, or `null` without an account;
- `cloud_error`: why the cloud delete failed, when it did.

```yaml
action: switchbot_art_frame.delete_image
target:
  entity_id: sensor.art_frame_eeff_local_images
data:
  image: oldest
response_variable: deleted
```

### download_image

Tells the frame to fetch an image that is already in SwitchBot's cloud for it,
as tapping **Sync** in the SwitchBot app does. The frame downloads it over its
own Wi-Fi. `show_image` covers the whole process including this step, so this
action is only needed for an image uploaded some other way.

Fields:

- `image_id` (required): the ID SwitchBot's cloud assigned to the image, 1 to
  255. The frame requests that ID from the cloud, so any other ID fails
  (download state 3).
- `show` (default `true`): show the image once it has downloaded.

The frame must have a free slot, and the ID must not already be on it.

If the download fails quickly (the frame refuses a command, reports a failed
download, or the Bluetooth connection drops), it is retried once after a few
seconds. A download still running after 2 minutes isn't retried: the frame may
yet finish it.

Response: `image_id`, `image_ids`, `current_image_id` and `progress`, as for
`show_image`.

```yaml
action: switchbot_art_frame.download_image
target:
  entity_id: sensor.art_frame_eeff_local_images
data:
  image_id: 16
```

### upload_image (experimental)

Uploads the image at a web address to SwitchBot's cloud for the frame, through
the SwitchBot Cloud integration, and returns SwitchBot's reply. It makes the
same call as `switchbot_cloud.upload_art_frame_image`, which discards the
reply. The frame doesn't fetch the image until told to with `download_image`.

Fields:

- `image_url` (required): a web address SwitchBot's servers can reach.

Response:

- `cloud_response`: SwitchBot's reply to the upload.

```yaml
action: switchbot_art_frame.upload_image
target:
  entity_id: sensor.art_frame_eeff_local_images
data:
  image_url: https://example.com/frame/today.jpg
response_variable: upload
```

## Dashboard card

<p>
  <img src="https://raw.githubusercontent.com/se0siris/ha-switchbot-art-frame/main/docs/card.webp" alt="The card, waiting for an image to be chosen" width="400" align="top">
  <img src="https://raw.githubusercontent.com/se0siris/ha-switchbot-art-frame/main/docs/card-preview.webp" alt="The card previewing an image before it's sent to the frame" width="400" align="top">
</p>

The integration serves and loads its own card, so there's no resource to add.
It sends images with `show_image`, so it needs the same SwitchBot Cloud
integration and account.

```yaml
type: custom:switchbot-art-frame-card
entity: sensor.art_frame_eeff_local_images
```

Options:

- `entity` (required): the frame's Local images sensor;
- `orientation`: `portrait` (default) or `landscape`;
- `size`: the frame's screen size in inches, `13.3` (default, 1200×1600),
  `7.3` (480×800) or `31.5` (1440×2560). The card can't detect it, so set it
  for the smaller and larger frames;
- `fit`: `fill` (default; crops the image to fill the frame) or `fit` (shows
  the whole image with white borders). This can also be switched on the card;
- `title`: the card's heading (default "Art Frame").

The card shows how many images the frame holds and, when it's full, notes that
sending will replace the oldest. Choose a PNG or JPEG to open a preview at the
frame's shape, then select **Show on frame**, or **Cancel** to close it. The
card resizes the image in the browser to the frame's resolution (for the
13.3" frame, 1200×1600, or 1600×1200 in landscape) and sends it as a PNG if
that fits under the upload limit, or as a JPEG otherwise. On the 31.5" frame
a detailed photo may need a fairly low JPEG quality to fit, and one that
still doesn't fit is refused with an error. Sending takes about a minute.

## How it works

The integration uses three channels:

- **Bluetooth**, through the core SwitchBot integration's device: reading the
  frame's status, showing and deleting images, and starting a download.
- **SwitchBot's public Cloud API**, through the SwitchBot Cloud integration:
  uploading an image (`uploadImage`, with an image URL or base64 data).
- **SwitchBot's private app API**, with the optional account: listing and
  deleting entries in the frame's cloud album, which is how `show_image`
  finds the ID SwitchBot assigned to an upload.

The Bluetooth commands were captured from the SwitchBot app with an Android
Bluetooth HCI snoop log and decrypted with the frame's key:

```
570F7A0102{image_id}     delete image   (reply: 01 <image_id> 1E)
570F7A00                 start Wi-Fi (the app's comment: "trigger device to connect")
570F7A0101{image_id}     download pending cloud image, store as image_id
570F7B00                 download progress (state 0 idle, 1 busy, 2 done, 3 failed)
570F7A02{image_id}       show image     (already in PySwitchbot)
5702                     status         (already in PySwitchbot)
```

The private API calls (host `wonderlabs.<region>.api.switchbot.net`, after
logging in at `account.api.switchbot.net`) all take `deviceID` (the frame's
MAC address) and `groupID` (its SwitchBot home):

```
device/artframe/api/v1/images/list          album: id, imageIndex, status (0 pending, 1 synced)
device/artframe/api/v1/images/delete        {imageIndexList, deleteAll}
productbiz/v1/slideshow/syncDeletedImages   {imageIndexes, deleteAll}
```

## Caveats

- It relies on internals of the core SwitchBot integration and PySwitchbot
  (the coordinator in `runtime_data`, and private device methods such as
  `_send_command`), and of the SwitchBot Cloud integration (its API client's
  `_request`, for uploads).
  A Home Assistant update could break it; the fix is usually small.
- New images still come from SwitchBot's cloud: the frame downloads them over
  its own Wi-Fi. If SwitchBot's servers or private API change or go away,
  adding images stops working. Images already on the frame are unaffected.
- SwitchBot rejects uploads larger than about 1 MiB.
- Without an account, a Bluetooth delete doesn't reach SwitchBot's cloud, and
  the app shows the image as waiting to sync.
- If the core SwitchBot entry reloads, this integration reloads with it.

## Tests

The development dependencies are pinned in `pyproject.toml` and `uv.lock`.
With [uv](https://docs.astral.sh/uv/):

```
uv sync
uv run pytest
```

The tests use a simulated frame that answers commands the way an Art Frame
does.
