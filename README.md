# Sentient AI — Emotion by Interruption (EBI-1)

A conversational demo of a **sentient-style chatbot** whose feelings change when the environment changes — not because you asked it to act angry or happy, but because a sensor crossed a threshold **while it was talking**.

The bot streams a reply like a person mid-sentence. If temperature, light, volume, humidity, smell, or surface touch jumps into a new band, it **cuts itself off**, blurts a reaction ("Huh", "Oh!", "Woah"), and continues the same thought in a new emotional tone.

## Objective

Show that emotion can be driven by **interruption**, not by a static mood slider.

Most chatbots pick a tone up front and hold it for the whole answer. This project treats emotion the way a body does: nothing happens while conditions stay in the "normal" band. The moment a sense crosses a threshold, the stream stops, a spoken reaction fires, the **Current Emotional State** updates (Joy, Sadness, Fear, Anger, Surprise, Disgust, or Neutral), and the remaining thought resumes in that tone.

The web page is the control room for that experiment: chat on the left, live environment on the right.

## How emotion by interruption works

```
You speak → Claude starts streaming a reply
                │
                ├── tokens print mid-sentence
                └── a monitor watches sensors every few seconds
                        │
                        threshold crossed?
                        no  → keep talking
                        yes → STOP mid-sentence
                              Type 1 utterance (Huh / Oh / Woah…)
                              optional Type 2 (shout / yawn / laugh)
                              comment about that sense
                              map sense → primary emotion
                              resume the thought in the new tone
```

Changes are **edge-triggered**. Holding a slider in "hot" does nothing after the first interrupt. Moving it from normal → hot (or hot → normal) is what fires the reaction.

Personality **Individual_001** (simple model) shapes how sensitive the bot is, how long it waits before noticing, and how colloquial vs. factual the interrupt line sounds.

## Requirements

- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com/)
- Windows 10 is fine (PowerShell)

## Setup and run

From the project folder:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and put your key in it:

```
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-5
```

Never commit `.env`. It is already listed in `.gitignore`.

Start the web app:

```powershell
python main.py
```

Then open [http://127.0.0.1:7860](http://127.0.0.1:7860).

Optional flags:

```powershell
python main.py --host 127.0.0.1 --port 7860
python main.py --cli
```

`--cli` is a terminal chat with `/set`, `/demo`, `/status`, and `/help` instead of the page.

## How to use the page

The left nav switches two screens that share the same chat and sensors.

### Main — Emotion by Interruption

This is the experiment.

1. **Chat** in the left column. Type a message and press **Send**. The bot streams its answer into the log. The header badge shows the current emotion and intensity; **idle** / **speaking** shows whether a reply is in flight.
2. **Draft the environment** on the right while it talks (or before you send). Sliders and buttons are drafts until they are applied:
   - **Temperature** — cold / normal / hot (touch)
   - **Lumens** — too dark / normal / too bright (vision)
   - **Hearing / volume** — quiet / normal / loud
   - **Humidity** — dry / normal / wet
   - **Smell** — none, flowers, or rotten eggs
   - **Surface touch** — none, soft, hard, cold, or hot (one-shot; it clears after it fires)
3. **Apply environment** commits the draft. If the bot is mid-sentence and a band changed, it will interrupt, speak the reaction, and continue. The pill next to the panel title reads **pending apply** until you apply, or **synced** when draft and live sensors match.
4. Leave **Apply selectors on send** checked if you want Send to apply the draft automatically (interrupt is deferred so it can land during the new reply). Uncheck it to chat without changing the room.
5. **Preview replies** asks the model for a before/after sample: current environment vs. your pending selectors. Use it to see the interrupt line and the next spoken reply without sending.
6. **Next utterance** (the two boxes) shows the canned interrupt text for the live sensors vs. the draft. It updates as you move sliders.
7. **Reset sensors** returns the environment to comfortable defaults and Neutral. **Clear chat** wipes the conversation log only.

A typical try: send "Tell me about your afternoon," then while tokens are appearing drag **Temperature** into the hot band and click **Apply environment**. You should see a mid-sentence cut (`--`), a line like "Oh! is it getting hot in here…", the badge flip toward Anger, and the rest of the answer in that tone.

Colored bars under each slider are the current bands. Emotion labels under those bars are read-only on Main.

### Edit Personality

This screen programs **how** the body maps the world to feeling. Chat still works.

- **Drag the handles** on the colored threshold tracks to widen or shrink cold / normal / hot (and the same for light, volume, humidity). A narrower "normal" band makes the bot interrupt more easily. Changes save to `thresholds.json`.
- **Emotion dropdowns** appear on each band and on smell / surface choices. Bind a band to Neutral, Joy, Sadness, Fear, Anger, Surprise, or Disgust. Bindings save to `emotion_map.json`.
- **Reset emotion map** and **Reset thresholds** restore the built-in defaults.

Default mappings (you can change them on this screen):

| Sense | Low / choice | Normal | High / choice |
| --- | --- | --- | --- |
| Temperature | cold → Sadness | Neutral | hot → Anger |
| Lumens | too dark → Fear | Neutral | too bright → Surprise |
| Volume | quiet → Surprise | Neutral | loud → Anger |
| Humidity | dry → Sadness | Neutral | wet → Disgust |
| Smell | none → Neutral | flowers → Joy | rotten eggs → Disgust |
| Surface | soft → Joy, hard → Surprise, cold → Surprise, hot → Anger | | |

## Project layout

```
main.py                 # web server (or --cli)
sentient_ai/            # personality, sensors, interrupt engine, Claude client
web/static/             # the page (index.html, app.js, styles.css)
emotion_map.json        # saved sense → emotion bindings (gitignored after local edits)
thresholds.json         # saved band cuts (gitignored after local edits)
.env.example            # template for the Anthropic key
```

The bot talks through Claude. Sensors, thresholds, interrupt lines, and emotion updates are local Python — they do not wait on the model to "decide" to feel something.
