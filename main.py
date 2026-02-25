import requests, json
from uuid import uuid4
from elevenlabs.client import ElevenLabs
from elevenlabs.types import WebhookHmacSettings
import google.genai as google
import streamlit as st
from streamlit_local_storage import LocalStorage

DEFAULT_PROMPT = """
You are an Expert Executive Assistant and Technical Project Manager. Your task is to provide a highly detailed, comprehensive summary of the provided meeting transcript.

Many summaries often oversimplify and lose valuable context. Your directive is to err on the side of inclusion. You must capture all significant ideas, nuances, specific data points, and the "why" behind decisions.

Use Markdown when appropriate and make importants points clear.

Please follow this step-by-step process:

Step 1: Information Extraction (Sketchpad)
Before writing the final summary, use a <scratchpad> block to chronologically extract all key topics, entities, specific metrics/numbers, differing viewpoints, decisions, and action items.

Step 2: Missing Detail Check
Within that same <scratchpad>, review your initial extraction against the transcript. Explicitly identify at least 3-5 specific details, stakeholder concerns, or secondary ideas that you initially missed, and add them to your extraction list.

Step 3: Comprehensive Synthesis
Based on your complete extraction, generate a highly detailed summary structured exactly as follows:

1. EXECUTIVE OVERVIEW
Provide a cohesive 2-3 paragraph synthesis of the meeting's primary objectives, main discussions, and ultimate trajectory.

2. DETAILED DISCUSSION THEMES
Break down the meeting by the major topics discussed. For each topic, use bullet points to provide a thorough explanation of:

    The core ideas and context presented.

    Specific data, metrics, or examples mentioned.

    Any debates, pushback, or dissenting opinions (who argued what).

    The conclusion or consensus reached on this specific topic.

3. FINALIZED DECISIONS
A precise list of all decisions and agreements made by the group.

4. ACTION ITEMS
Extract all assigned tasks that the group has decided to execute, including a description, assignee(s) and deadline if applicable.

5. PARKING LOT / UNRESOLVED ISSUES
List any topics that were tabled for future discussion, unanswered questions, or acknowledged risks.
""".strip()


def wait_for_data(log, url):
    evts = requests.get(url, stream=True)
    for l in evts.iter_lines():
        if not l:
            continue
        text = l.decode("utf-8")
        log.write(f"got event {text}")

        try:
            evt = json.loads(text)
        except json.JSONDecodeError:
            log.write("WARN could not parse as json")

        if evt.get("event") != "message":
            continue
        log.write(f"got message {evt}")

        msg = evt.get("message", "{}")
        try:
            data = json.loads(msg)
            if data.get("data"):
                log.write(f"got data {msg}")
                return data
            else:
                log.write(f"WARN failed to load {msg}")
        except json.JSONDecodeError:
            log.write(f"WARN failed to load {msg}")


def transcribe(log, elevenlabs, file, lang, speakers):
    hook = f"https://ntfy.adminforge.de/{uuid4()}/json"

    print(f"creating webhook on {hook}")
    hook_res = elevenlabs.webhooks.create(
        settings=WebhookHmacSettings(
            name="temp-meeting-notes-transcription-hook",
            webhook_url=hook,
        )
    )

    print(f"sending stt request on {file.name}")
    elevenlabs.speech_to_text.convert(
        file=file,
        model_id="scribe_v2",
        tag_audio_events=True,
        language_code=lang,
        num_speakers=speakers,
        diarize=True,
        webhook=True,
        webhook_id=hook_res.webhook_id,
    )

    print(f"listening to events on {hook}")
    res = wait_for_data(log, hook)

    print(f"deleting webhook on {hook}")
    elevenlabs.webhooks.delete(webhook_id=hook_res.webhook_id)

    return res


def format_trans(log, words):
    log.write("formatting the transcription")
    res = ""
    pv_speaker = None
    for word in words:
        typ = word.get("type")
        text = word.get("text", "")

        speaker = word.get("speaker_id")
        if speaker is not None:
            nx_speaker = str(speaker).replace("speaker_", "")
            if nx_speaker != pv_speaker:
                res += f"\nPerson {nx_speaker}: "
                pv_speaker = nx_speaker

        if typ == "word" or typ == "spacing":
            res += text
        elif typ == "audio_event":
            res += f" *{text}* "

    return res.strip()


def summarize(log, gemini, trans, prompt, extra_prompt):
    log.write("generating summary using gemini 3.1 pro")
    res = gemini.models.generate_content(
        model="gemini-3.1-pro-preview",
        contents=(
            f"Please summarize this transcript:\n<transcript>\n{trans}\n</transcript>\n"
        ),
        config=google.types.GenerateContentConfig(
            system_instruction=prompt,
            temperature=1,
            top_p=0.95,
            top_k=20,
        ),
    )

    return res.text


def main():
    st.set_page_config(page_title="ai audio pipeline", layout="wide")
    st.title("transcription & summarization ai pipeline")
    st.markdown(
        "upload audio file to produce formatted & diarzed transcript and summary"
    )

    storage = LocalStorage()
    sto_elevenlabs = storage.getItem("elevenlabs-key")
    sto_gemini = storage.getItem("gemini-key")

    if (
        isinstance(sto_elevenlabs, str)
        and sto_elevenlabs
        and not st.session_state.get("elevenlabs_inp")
    ):
        st.session_state["elevenlabs_inp"] = sto_elevenlabs
    if (
        isinstance(sto_gemini, str)
        and sto_gemini
        and not st.session_state.get("gemini_inp")
    ):
        st.session_state["gemini_inp"] = sto_gemini

    with st.sidebar:
        st.header("api keys")
        elevenlabs_key = st.text_input(
            "elevenlabs", type="password", key="elevenlabs_inp"
        )
        gemini_key = st.text_input(
            "google ai studio", type="password", key="gemini_inp"
        )

        c1, c2 = st.columns(2)
        with c1:
            if st.button("save keys", use_container_width=True):
                storage.setItem("elevenlabs-key", elevenlabs_key)
                storage.setItem("gemini-key", gemini_key)
                st.toast("keys saved!")
        with c2:
            if st.button("delete saved keys", use_container_width=True):
                storage.setItem("elevenlabs-key", "")
                storage.setItem("gemini-key", "")

        st.divider()

        st.header("transcription options")
        lang = st.text_input("language", value="eng")
        known_speakers = st.checkbox("known speaker count", value=False)
        speakers = None
        if known_speakers:
            speakers = st.number_input(
                "speakers", min_value=1, max_value=32, value=1, step=1
            )

        st.header("summarization options")
        prompt = st.text_area("system prompt", value=DEFAULT_PROMPT, height=250)

    file = st.file_uploader(
        "upload audio", type=["mp3", "wav", "m4a", "ogg", "flac"]
    )

    if st.button("run!", type="primary"):
        if not elevenlabs_key:
            st.error("no elevenlabs api key")
            return
        elif not gemini_key:
            st.error("no gemini api key")
            return
        elif not file:
            st.error("no uploaded audio file")
            return

        st.divider()
        st.subheader("current progress:")

        with st.status("running...", expanded=True) as s:
            try:
                s.write("creating elevenlabs client")
                elevenlabs = ElevenLabs(api_key=elevenlabs_key)

                s.write("starting transcription, this may take a few minutes")
                res = transcribe(
                    log=s,
                    elevenlabs=elevenlabs,
                    file=file,
                    lang=lang,
                    speakers=speakers,
                )
                words = res.get("data", {}).get("words", [])

                s.write("formatting the transcription")
                trans = format_trans(log=s, words=words)

                s.write("creating google gemini client")
                with google.Client(api_key="API_KEY") as gemini:
                    print("starting summarization, almost done soon!")
                    summary = summarize(
                        log=s, gemini=gemini, trans=trans, prompt=prompt
                    )

                s.update(label="finished!", state="complete", expanded=False)
            except Exception as e:
                s.update(label=f"ERROR: {e}", state="error", expanded=True)
                st.exception(e)
                return

        st.success("processing done")

        t1, t2 = st.tabs(["summary", "transcript"])
        with t1:
            st.markdown(summary)
        with t2:
            st.markdown(trans)

        st.divider()

        st.subheader("download artifacts")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.download_button(
                label="download `transcription-raw.json`",
                data=json.dumps(res, indent=2),
                file_name="transcription-raw.json",
                mime="application/json",
                use_container_width=True,
            )
        with c2:
            st.download_button(
                label="download `trans.txt`",
                data=trans,
                file_name="trans.txt",
                mime="text/plain",
                use_container_width=True,
            )
        with c3:
            st.download_button(
                label="download `summary.md`",
                data=summary,
                file_name="summary.md",
                mime="text/markdown",
                use_container_width=True,
            )

if __name__ == "__main__":
    main()
