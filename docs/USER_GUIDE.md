# PixelAgent user guide

[简体中文](USER_GUIDE.zh-CN.md) · Windows preview · Updated 18 November 2026

PixelAgent turns a character description or reference image into a pixel-art sprite. You review the plan, choose a concept when prompted, and approve the finished sprite. You can then revise a selected area, make expressions, and export images or a game pack.

This guide follows the current English interface. The main supported workflow is **characters and expressions**. Objects remain available as a retained workflow. Animations, scenes, and kits are experimental and hidden by default.

## 1. Choose how to run it

| Download or setup | What you need |
| --- | --- |
| **PixelAgent-Windows-x64.zip** release asset | Extract the whole ZIP into a writable folder and open **PixelAgent.exe**. Python and the drawing engine are bundled. |
| **Source code** ZIP or checkout | Install Python and dependencies, obtain the matching PixelLiner MCP engine, and configure its location. See [Run from source](#11-run-from-source). A source ZIP is not the Windows application. |

The desktop interface opens in an Edge or Chrome app window, or your default browser. It connects to a small server on your own computer. The Windows application normally uses `127.0.0.1:8765`; it can use another local port if that one is occupied.

Projects and settings live outside the extracted application folder. Moving the executable alone does not move your projects. The separate **PixelLiner editor** is optional: you only need it for **Open in PixelLiner**. It is distinct from the drawing engine bundled with PixelAgent.

## 2. Try the complete workflow for free

1. Open **Settings** in the sidebar.
2. Under **Mode**, select **Test run**. Keep **Look → Soft** and **Default height → 160 px**, then click **Save settings**.
3. Click **New project**, enter a name, and click **Create project**.
4. In **Characters**, click **New character**. Choose **Describe**, enter a description, and click **Create character**.
5. At **Check the plan**, review the details and click **Continue**.
6. If **Pick a concept** appears, select a thumbnail and click **Use this concept**.
7. At **Approve the sprite**, click **Approve and export**.
8. In **Expressions**, click **Make expressions**. Select the faces you want, then click **Make expressions** in the dialog.
9. In **Exports**, click **Save** beside **Game pack (ZIP)**.

**Test run uses free stand-ins and placeholder pictures.** It is useful for learning the controls and checking that saving and exporting work; its pictures do not demonstrate OpenAI generation quality. It does not require an API key. Switching to OpenAI does not replace existing test pictures automatically—create a new character for your first real generation.

## 3. Enable OpenAI generation

OpenAI mode sends prompts and relevant images to OpenAI and incurs API charges. You need internet access, your own OpenAI API key, and an API account with billing or usable credits and access to the configured models.

1. Open **Settings** and paste your key into **API key**.
2. Click **Save key**. This is separate from **Save settings**.
3. Choose **Mode → OpenAI**.
4. Review **Monthly limit** and **Limit per item**, then click **Save settings**.

The key is stored in your Windows user environment, not in project files. **Found** means a key is present; it does not confirm that the key works, that the required models are available, or that the account has credit. Never include the key when sharing a project or reporting a problem.

## 4. Make a character

Click **New character** and choose a starting point:

- **Describe:** describe one character, including clothing, colours, pose, and important identity details. For example: “A forest courier with a short green cloak, a red scarf, a leather satchel, and a calm expression. Full body, three-quarter view.”
- **Reference image:** drop or choose a PNG, JPG, or WEBP image. Add optional notes such as “keep the red scarf.” A clear full-body view makes the intended design easier to communicate. A scene or partial figure may be redrawn into a clean character concept.

**Name** is optional. **Height** offers 64, 96, 128, and 160 px. Start with 160 px when faces and expressions matter; smaller sprites have fewer pixels for facial features. The exported canvas can include transparent margins around the figure.

### Check the plan

Review **Name**, **Height**, **Description**, **View**, and **Must keep**. Put one essential feature on each line of **Must keep**. Click **Continue** when the plan describes the character you want.

### Pick a concept

When the concept picker appears, click a thumbnail to preview it, then choose **Use this concept**. If none is right, write a correction in **Not right? Say what to change** and click **New concepts**. New concepts can incur additional charges in OpenAI mode. Some single-concept paths proceed without this picker.

### Approve the sprite

Inspect the result at **1×** as well as a larger zoom. Check the silhouette, face, clothing, and details you asked to keep. **Quality check** is a model assessment, so use your own judgement before accepting the sprite.

Click **Approve and export** to finish. The item becomes **Ready**. The **Needs you** filter finds items awaiting approval, paused items, and stopped runs; **Working** shows active runs.

### Choose a look

| Setting | Result |
| --- | --- |
| **Soft** | The default character workflow. An image model draws pixel art, which PixelAgent recovers on its pixel grid and adjusts to the chosen palette. Use this for expressions. |
| **Classic** | A stricter palette and outline workflow in which the agent draws through the PixelLiner tools. Its processing and cost differ from Soft. The current expression workflow does not support Classic characters. |
| **Colours** | Soft-only palette adjustment: **None**, **Light**, **Medium**, or **Strong**. None keeps the drawn colours; stronger settings mute colours and lift shadows. |

Choose the look before creating a character. Changing Settings does not automatically regenerate an existing sprite.

## 5. Revise a sprite or revisit a version

For a change to the current drawing, type a clear instruction in **Revise** and click **Revise**. For a local change:

1. Click **Select an area**.
2. Drag a rectangle on the sprite.
3. Describe the change, such as “make this belt buckle gold.”
4. Click **Fix this area** and inspect the result before approving it.

The area operation confines the finished edit to the selected sprite pixels. Use the × on the area chip to clear the selection. Revisions cost money in OpenAI mode.

To rerun a stage, use the circular **Redo from here** control beside that stage in **Progress**, then confirm **Redo**. That stage and the later stages run again. Redoing **Concept** is appropriate when the design needs to change; revising a small area is appropriate for a local correction. The confirmation shows an estimate when applicable.

**Versions** shows earlier drawings when available. Click one to inspect it; **Show current** returns to the active drawing. **Use this version** is available only for versions compatible with the current plan and concept. Restoring one runs the later checks again and returns to approval; those checks may cost money in OpenAI mode. Dimmed versions remain viewable but cannot be restored into an incompatible plan.

## 6. Make expressions

Expressions require a finished **Soft** character with a visible face. A fully closed helmet or a face that cannot be located may prevent expression generation.

1. Open the character and find **Expressions**.
2. Click **Make expressions**, or **Make more** if expressions already exist.
3. Select **Happy**, **Sad**, **Angry**, **Surprised**, **Hurt**, or **Blink**. On the first use, all six presets are selected; deselect any you do not want.
4. Optionally describe a face under **Your own** and give it a short label under **Its name**.
5. Check the selected faces and estimate, then click **Make expressions**.

The character's body stays unchanged while the face is edited. A failed face check can trigger one automatic redraw, which can add cost. **Check this face** marks a failed check; **Note** shows a less severe finding. Review the face yourself, particularly skin colour, eyes, glasses, and hair.

Each expression has **PNG** for the whole sprite and **Face** for the face patch. Clicking its preview opens the enlarged sprite. Making the same expression again replaces the current result for that name. To keep a separate usable copy, save it before regenerating.

Expressions belong to the source character they were made from. After a revision or source change, incompatible expressions are hidden from the current list and excluded from new game packs. Their earlier files are retained in the character's folder. Follow the message to make a new set for the current character.

## 7. Save and use your work

After approval, the **Exports** card offers:

| Control | What it provides |
| --- | --- |
| **Sprite (PNG) → Save** | The transparent sprite at its native pixel size. |
| **Sprite 4× (PNG) → Save** | A larger preview with crisp enlarged pixels. |
| **Metadata (JSON) → Save** | Canvas size, palette, layer names, and pivot information. |
| **Game pack (ZIP) → Save** | An atlas containing the standing sprite and compatible expressions, frame metadata, a Godot 4 SpriteFrames resource, and import notes. Existing finished animations can also be included. |
| **Show in folder** | The current export folder, including layer PNGs and the layered project. |
| **Open in PixelLiner** | Opens the exported image in the separately installed PixelLiner editor. |

Saving existing exports and building a game pack are local operations with no model calls. **Approve and export** at the final sprite approval is also free. Earlier approvals that start generation are paid in OpenAI mode.

Extract a game pack and read its `README.txt`: it gives the actual cell size, pivot, filenames, and import settings for that pack. The JSON uses Aseprite/TexturePacker-style frame metadata; it is not an `.aseprite` project. For a game, use the native-size sprite or atlas with nearest-neighbour/point filtering. Keep the atlas and accompanying metadata together. Rebuild the game pack after changing the sprite or expressions; an already downloaded ZIP does not update itself.

## 8. Understand costs and stopping

Amounts labelled **About … (estimate)** are rough forecasts, not quotes. **Spent** and the sidebar's monthly total are local estimates calculated from returned API usage and configured prices. Retries, new concepts, revisions, and repeated expressions can add to the total. The OpenAI billing dashboard determines the final charge.

- **Monthly limit** applies across the app's projects. A fresh installation starts at US$90.
- **Limit per item** includes work on that item, including redraws and expressions. A fresh installation starts at US$3.
- Limits stop new requests after recorded spending reaches the threshold. A request already in progress can finish above the limit. These settings are not a provider-side billing cap.
- API credit and ChatGPT subscriptions are separate account matters. PixelAgent does not determine your credit entitlement from your plan name.

Click **Cancel** to stop a run. **Stopping…** means it is waiting for the current operation; cancellation cannot undo a request already sent or refund its cost. Finished work is kept. Use **Continue** when offered, or correct an error and choose **Try again**. For interrupted expressions, reopen **Make more** or **Make expressions** and select the faces still needed.

You can leave a character page while it is working. Closing the desktop window is not a reliable way to cancel: active work can continue. Use **Cancel** first if you want it to stop.

## 9. Storage, backups, and deletion

By default, projects are stored under your **Documents\PixelAgent** folder. **Settings → Projects folder** chooses the parent folder containing projects; changing it does not move existing projects. To move work, close the app, copy the project folders to the new location, and select that parent folder in Settings.

Each project contains its assets, saved steps, versions, exports, and a `costs.jsonl` usage log. Use **Show in folder** to locate it and back up the entire project folder. Back up projects before editing internal files.

The **…** menu offers **Rename…**, **Duplicate**, **Show in folder**, and **Delete…**. Deletion moves work to **Recently Deleted**, where **Restore** brings it back. **Delete Now** and **Delete All** permanently remove the selected deleted work. Renaming, duplicating, or deleting work does not refund API charges or reset the monthly spending record.

Settings and the app-wide spending ledger live in `%USERPROFILE%\.pixelagent`. The windowed application's diagnostic log is `app.log` in that folder. The API key is stored separately in the Windows user environment.

## 10. Troubleshooting

| What you see | What to do |
| --- | --- |
| No executable in the download | Download the **PixelAgent-Windows-x64.zip** release asset. GitHub's source ZIP requires the source setup below. |
| Placeholder art | Check **Settings → Mode**. Test run intentionally uses stand-ins. Switch to OpenAI and create a new character for a real generation. |
| API key **Not set** | Paste your own key and click **Save key**. Saving other settings does not save a key. |
| An API error or HTTP status in **Details** | Check internet access, key validity, model access, and API billing/quota. After resolving the cause, use **Try again**. Repeated retries do not fix missing credit or access. |
| **Stopped at a spending limit** | Check **Spent**, **Monthly limit**, and **Limit per item**. Raise a limit only if you intend to spend more, save Settings, then retry. Existing exports can still be saved. |
| Missing pricing or an unknown model error | A custom model needs a valid pricing configuration and compatible API support. Restore the supplied defaults or update the project configuration before retrying. |
| Missing/invalid usage or an unknown-cost error | The request may already have incurred a charge. Check provider billing before deliberately starting another job. Do not interpret an unknown cost as zero. |
| **Expressions need the Soft look** | Use a character created through Soft. Changing the setting alone does not convert an existing Classic character. |
| Old expressions disappear after a revision | They do not match the current character. Their files are retained; make a new set before exporting them. |
| **Use this version** is unavailable | The drawing was made from an earlier plan or concept. It can be viewed but is not compatible with the current state. |
| PixelLiner cannot be found | The separate editor is optional. Save PNGs or use **Show in folder**. Advanced users can set its executable location in the project configuration. |
| Source run reports a missing engine | Check the engine checkout and `[engine] path`, including the presence of `canvas.py`. The portable release already bundles this engine. |
| App window does not open | Relaunch the app and check `%USERPROFILE%\.pixelagent\app.log`. For source runs, read the local URL printed in the terminal. |

When reporting a problem, include the app version, Windows version, Test run/OpenAI mode, the steps to reproduce, and the visible error. Remove personal references or account information from attachments. Never share an API key.

## 11. Run from source

This is the developer setup. The Windows release ZIP is the simpler option when you only want to use the app.

1. Obtain the PixelAgent source checkout and a matching PixelLiner MCP engine checkout. Engine access is separate; the source ZIP does not include the engine. The verified engine revision for this preview is `0c24db40e84d797fb2418444ecb96184fb53231b`; confirm it against `packaging/engine-lock.json` in your checkout.
2. Install Python **3.11 or newer**. The Windows preview was verified with Python 3.13. From the PixelAgent source folder, create an environment and install the application:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e ".[openai]"
   ```

3. In the source folder's `config.toml`, set `[engine] path` to your engine folder—the directory containing `canvas.py` and `server.py`. For example:

   ```toml
   [engine]
   path = "C:/dev/pixelliner-mcp"
   ```

   Replace the example path with your own. Edit the existing section rather than adding a duplicate. Keep the other keys in that section.

4. Launch from the source folder:

   ```powershell
   .\.venv\Scripts\python.exe -m pixelagent.app
   ```

5. Follow the free Test run walkthrough first. Configure an API key in the app only when you want paid generation.

The checkout's `requirements-lock.txt` records the tested Windows Python 3.13 environment for reproducible builds. A project's own `config.toml` can override model selections and prices; existing explicit model choices are preserved. This is an advanced setting, and the desktop app's mode, look, and budget settings still govern app runs.

See [Windows verification and measured example costs](WINDOWS_VERIFICATION_2026-11-18.md) for the tests performed on this preview and the remaining verification limits. Source installation is not a promise of a tested macOS or Linux desktop release.
