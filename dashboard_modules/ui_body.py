import gradio as gr

def render_script_ui(script_data):
    """
    Dynamically renders the ScriptBrain JSON output into nested Accordions and Markdown.
    Takes the raw JSON output from the ScriptBrain engine.
    """
    if not script_data:
        # Initial empty state
        gr.Markdown("*等待生成剧本... (Waiting for script generation...)*")
        return

    episodes = script_data.get("episodes", [])
    if not episodes:
        raise gr.Error("Script format error: 'episodes' key missing or empty.")

    for ep in episodes:
        with gr.Accordion(f"Episode {ep.get('episode_number', '?')}: {ep.get('title', 'Untitled')}", open=True):
            shots = ep.get("shots", [])
            for shot in shots:
                shot_id = shot.get("shot_id", "Unknown")

                with gr.Accordion(f"🎬 Shot {shot_id}", open=False):
                    scene_setting = shot.get("scene_setting", "")
                    narrative_action = shot.get("narrative_action", "")
                    midjourney_prompt = shot.get("midjourney_prompt", "")

                    subtasks = shot.get("subsystem_tasks", {})

                    if not subtasks:
                        raise gr.Error(f"Missing 'subsystem_tasks' in Shot {shot_id}")

                    identity = subtasks.get("1_identity_dna", {})
                    scene = subtasks.get("2_scene_dna", {})
                    performance = subtasks.get("3_performance_dna", {})
                    vocal = subtasks.get("4_vocal_dna", {})
                    audio = subtasks.get("5_audio_dna", {})

                    markdown_content = f"""
**Scene Setting:** {scene_setting}

**Narrative Action:** {narrative_action}

---
### 👤 Identity DNA
* **Character Name:** {identity.get('character_name', '')}
* **Appearance & Clothing:** {identity.get('appearance_and_clothing', '')}
* **Facial Micro-expression:** {identity.get('facial_micro_expression', '')}

### 🖼️ Scene DNA
* **Location Details:** {scene.get('location_details', '')}
* **Cinematic Lighting:** {scene.get('cinematic_lighting', '')}

### 🎬 Performance DNA
* **Camera Work:** {performance.get('camera_work', '')}
* **Subject Physics:** {performance.get('subject_physics', '')}

### 🎙️ Vocal DNA
* **Dialogue Text:** {vocal.get('dialogue_text', '')}
* **Vocal Emotion Tags:** {vocal.get('vocal_emotion_tags', '')}

### 🎵 Audio DNA
* **Foley SFX:** {audio.get('foley_sfx', '')}
* **BGM Mood:** {audio.get('bgm_mood', '')}

---
**Midjourney Prompt:** `{midjourney_prompt}`
"""
                    gr.Markdown(markdown_content)

def create_ui_body(script_state_component):
    """
    Creates the main rendering zone for the script, bound to a State component.
    script_state_component should be a gr.State containing the JSON dictionary.
    """
    with gr.Column(scale=3):
        # We use @gr.render via the input state to dynamically build the UI
        @gr.render(inputs=[script_state_component])
        def dynamic_render(script_data):
            try:
                render_script_ui(script_data)
            except Exception as e:
                raise gr.Error(f"UI Rendering failed: {str(e)}")
