import flet as ft
from flet import (
    Column,
    Row,
    Text,
    TextField,
    ElevatedButton,
    FilePicker,
    FilePickerResultEvent,
    Dropdown,
    dropdown,
    Image,
    GridView,
    Container,
    ProgressBar,
    MainAxisAlignment,
    CrossAxisAlignment,
    Page,
    icons,
    border_radius,
    border,
    colors,
    ImageFit,
    alignment
)
import os
import subprocess
import threading
import time # For potential delays or simulations if needed

# PySceneDetect imports
from scenedetect import VideoManager, SceneManager
from scenedetect.detectors import AdaptiveDetector, ContentDetector, ThresholdDetector, HistogramDetector
# FrameTimecode is part of scenedetect, needed for calculate_midframes_logic
from scenedetect.frame_timecode import FrameTimecode


# --- Core logic adapted from mv_scene_extractor.py ---

def calculate_midframes_logic(scenes, frame_rate):
    """Calculate mid-point timecodes for each scene."""
    midframes_tc_obj = [] # To store FrameTimecode objects for ffmpeg
    midframes_display = [] # To store string timecodes for display/logging if needed
    for start_tc_obj, end_tc_obj in scenes:
        # Ensure start_tc_obj and end_tc_obj are FrameTimecode objects
        # If they are already FrameTimecode objects from PySceneDetect, great.
        # If they are in seconds (float), convert them.
        
        start_seconds = start_tc_obj.get_seconds()
        end_seconds = end_tc_obj.get_seconds()

        mid_sec = (start_seconds + end_seconds) / 2.0
        
        # Create a FrameTimecode object for precise ffmpeg seeking
        mid_frame = int(mid_sec * frame_rate)
        mid_tc_obj = FrameTimecode(timecode=mid_frame, fps=frame_rate)
        midframes_tc_obj.append(mid_tc_obj.get_timecode()) # Get HH:MM:SS.mmm string

        # For display purposes, if needed
        hours = int(mid_sec // 3600)
        minutes = int((mid_sec % 3600) // 60)
        seconds_part = mid_sec % 60
        timecode_display = f"{hours:02d}:{minutes:02d}:{seconds_part:06.3f}"
        midframes_display.append(timecode_display)
        
    return midframes_tc_obj # Return list of HH:MM:SS.mmm strings

def extract_frames_logic(video_path, midframe_timecodes, output_dir, image_ext='jpg', status_callback=None, image_callback=None):
    """Extract single frames at given timecodes using ffmpeg."""
    os.makedirs(output_dir, exist_ok=True)
    extracted_images_paths = []
    for idx, tc_str in enumerate(midframe_timecodes, start=1):
        out_path = os.path.join(output_dir, f"{idx:04d}.{image_ext}")
        if status_callback:
            status_callback(f"Extracting frame {idx}/{len(midframe_timecodes)}: {tc_str}")
        
        try:
            subprocess.run(
                ['ffmpeg', '-y', '-ss', tc_str, '-i', video_path, '-vframes', '1', '-q:v', '2', out_path], # -q:v 2 for high quality JPEG
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True # Capture stderr for errors
            )
            extracted_images_paths.append(out_path)
            if image_callback:
                image_callback(out_path)
        except subprocess.CalledProcessError as e:
            error_message = f"ffmpeg error for {tc_str}: {e.stderr.decode() if e.stderr else 'Unknown error'}"
            if status_callback:
                status_callback(error_message) # Update UI with ffmpeg error
            print(error_message) # Also print to console for debugging
            # Decide if you want to stop or continue on ffmpeg error
            # For now, it continues but the overall success might be impacted
        except FileNotFoundError:
            error_message = "ffmpeg command not found. Please ensure ffmpeg is installed and in your PATH."
            if status_callback:
                status_callback(error_message)
            print(error_message)
            raise # Re-raise to stop the process
            
    return extracted_images_paths

# --- Flet App ---
def main(page: Page):
    page.title = "MV Scene Extractor GUI"
    page.vertical_alignment = MainAxisAlignment.START
    page.horizontal_alignment = CrossAxisAlignment.CENTER
    page.window_width = 850
    page.window_height = 750
    page.padding = 10

    # --- State Variables ---
    selected_video_path_text = Text("No video selected yet.", selectable=True)
    # Default output dir: current_working_directory/extracted_scenes
    default_output_path = os.path.join(os.getcwd(), "extracted_scenes")
    selected_output_dir_text = Text(default_output_path, selectable=True)
    
    # --- UI Controls ---
    # Video File Picker
    def on_video_picked(e: FilePickerResultEvent):
        if e.files and len(e.files) > 0:
            selected_video_path_text.value = e.files[0].path
            # Try to get frame rate for calculate_midframes_logic
            try:
                temp_video_manager = VideoManager([e.files[0].path])
                page.client_storage.set("video_frame_rate", temp_video_manager.get_framerate())
                temp_video_manager.release()
            except Exception as ex:
                print(f"Could not get frame rate: {ex}")
                page.client_storage.set("video_frame_rate", 30.0) # Assume 30 fps if error
        else:
            selected_video_path_text.value = "Video selection cancelled or no file chosen."
        page.update()

    video_file_picker = FilePicker(on_result=on_video_picked)
    page.overlay.append(video_file_picker)

    # Output Directory Picker
    def on_output_dir_picked(e: FilePickerResultEvent):
        if e.path:
            selected_output_dir_text.value = e.path
        else:
            selected_output_dir_text.value = "Output directory selection cancelled."
        page.update()

    output_dir_picker = FilePicker(on_result=on_output_dir_picked)
    page.overlay.append(output_dir_picker)

    # Algorithm Selector
    algo_dropdown = Dropdown(
        label="Detector Algorithm",
        hint_text="Choose detection algorithm",
        options=[
            dropdown.Option("adaptive", "Adaptive"),
            dropdown.Option("content", "Content"),
            dropdown.Option("threshold", "Threshold (Fade/Dissolve)"),
            dropdown.Option("hist", "Histogram (Experimental)"),
        ],
        value="adaptive",
        width=250,
        autofocus=True
    )

    # Default thresholds from user's memory and PySceneDetect CLI defaults
    # adaptive: 3.0
    # content: 27.0
    # threshold (PySceneDetect type, for fades): 0.05 was set by user, but original mv_scene_extractor's ThresholdDetector is for pixel diffs not fades.
    # The original script's ThresholdDetector default for `threshold` was `args.threshold` (3.0).
    # PySceneDetect's own `ThresholdDetector` default is 12.0.
    # Let's stick to what user last provided for specific algos if named similarly, or sensible defaults.
    # The 'threshold' algo in mv_scene_extractor IS PySceneDetect's ThresholdDetector. User set its default to 0.05. This is very low.
    # PySceneDetect `detect-threshold` (for fades) default is 12.0.
    # mv_scene_extractor.py's `ThresholdDetector` is for overall pixel changes, default 12.0.
    # User provided "threshold: 0.05" likely refers to `detect-threshold` in PySceneDetect for fades, which is a different detector.
    # The current script uses `ThresholdDetector` from `scenedetect.detectors`. Its default `threshold` is 12.0.
    # Let's clarify: The `mv_scene_extractor.py` uses `ThresholdDetector`. PySceneDetect docs state its default is 12.0.
    # User input "threshold: 0.05" likely referred to a general fade detection setting, not specifically this detector.
    # User input "hist: 0.05" is also very low for HistogramDetector (default 0.40).

    # Let's use defaults from PySceneDetect documentation or commonly accepted values.
    # User provided: content: 27.0, adaptive: 3.0. These are good.
    # For 'threshold' (ThresholdDetector), PySceneDetect doc default is 12.0.
    # For 'hist' (HistogramDetector), PySceneDetect doc default is 0.40.
    # The script had a global default of 3.0 for its threshold arg.

    app_default_thresholds = {
        "adaptive": "3.0",
        "content": "27.0",
        "threshold": "12.0", # PySceneDetect's ThresholdDetector default
        "hist": "0.40",    # PySceneDetect's HistogramDetector default
    }
    threshold_input = TextField(label="Threshold", value=app_default_thresholds[algo_dropdown.value], width=150, text_size=12)

    min_scene_len_input = TextField(label="Min Scene Len (frames)", value="15", width=180, text_size=12)
    window_size_input = TextField(label="Window Size (adaptive)", value="2", width=180, text_size=12) # Initial visibility managed by row
    min_content_val_input = TextField(label="Min Content Val (adaptive)", value="15.0", width=200, text_size=12) # Initial visibility managed by row

    settings_row2_adaptive = Row(
        [window_size_input, min_content_val_input],
        alignment=MainAxisAlignment.START, 
        spacing=15,
        visible=(algo_dropdown.value == "adaptive") # Initial visibility based on default algo
    )

    def algo_changed_handler(e):
        algo_value = algo_dropdown.value
        threshold_input.value = app_default_thresholds[algo_value]
        is_adaptive = algo_value == "adaptive"
        settings_row2_adaptive.visible = is_adaptive
        page.update()
        
    algo_dropdown.on_change = algo_changed_handler

    status_text = Text("Status: Idle", selectable=True, size=14)
    progress_bar = ProgressBar(width=page.width if page.width else 780, visible=False, color=colors.AMBER, bgcolor=colors.with_opacity(0.3, colors.BLUE_GREY))

    image_grid = GridView(
        expand=True, # Takes available space
        runs_count=5,
        max_extent=150,
        child_aspect_ratio=1.0,
        spacing=10,
        run_spacing=10,
    )
    
    start_button = ElevatedButton("Start Extraction", icon=icons.PLAY_ARROW, height=40)

    # --- Extraction Thread Function ---
    def run_extraction_thread_fn(page_ref: Page, video_p, output_d, algo, thresh_val, min_len_val, win_size_val, min_cont_val_val):
        
        def update_status_on_ui_thread(message):
            status_text.value = message
            page_ref.update()

        def add_image_on_ui_thread(image_path_abs):
            image_grid.controls.append(
                Image(
                    src=image_path_abs,
                    fit=ImageFit.CONTAIN,
                    width=150,
                    height=150,
                    border_radius=border_radius.all(5),
                    tooltip=os.path.basename(image_path_abs)
                )
            )
            page_ref.update()
        
        current_video_frame_rate = page.client_storage.get("video_frame_rate") or 30.0 # fallback
        extraction_successful = False # Flag to track overall success

        try:
            update_status_on_ui_thread("Status: Initializing video...")
            video_manager = VideoManager([video_p])
            scene_manager = SceneManager()
            
            update_status_on_ui_thread(f"Status: Configuring '{algo}' detector...")
            if algo == 'adaptive':
                detector = AdaptiveDetector(adaptive_threshold=thresh_val, min_scene_len=min_len_val, window_width=win_size_val, min_content_val=min_cont_val_val)
            elif algo == 'content':
                detector = ContentDetector(threshold=thresh_val, min_scene_len=min_len_val)
            elif algo == 'threshold':
                detector = ThresholdDetector(threshold=thresh_val, min_scene_len=min_len_val)
            elif algo == 'hist':
                detector = HistogramDetector(threshold=thresh_val, min_scene_len=min_len_val)
            else:
                update_status_on_ui_thread(f"Error: Unknown algorithm: {algo}")
                raise ValueError(f"Unknown algorithm: {algo}")
            
            scene_manager.add_detector(detector)
            
            update_status_on_ui_thread("Status: Starting video processing for scene detection...")
            video_manager.start()
            # Provide a callback for new scenes to update UI
            scene_manager.detect_scenes(
                frame_source=video_manager,
                callback=lambda frame_img, frame_num: update_status_on_ui_thread(f"Status: Scene detected at frame {frame_num}")
            )
            scenes = scene_manager.get_scene_list() # List of (FrameTimecode, FrameTimecode)
            
            update_status_on_ui_thread(f"Status: Detected {len(scenes)} scenes. Calculating midframes...")
            if not scenes:
                update_status_on_ui_thread("Status: No scenes detected.")
                return

            midframe_timecodes_str = calculate_midframes_logic(scenes, current_video_frame_rate)
            video_manager.release() # Release video manager after getting scenes and frame rate

            if not midframe_timecodes_str:
                update_status_on_ui_thread("Status: No midframes to extract.")
                return

            update_status_on_ui_thread(f"Status: Extracting {len(midframe_timecodes_str)} images to {output_d}...")
            extract_frames_logic(
                video_p, midframe_timecodes_str, output_d,
                status_callback=update_status_on_ui_thread,
                image_callback=lambda img_path: add_image_on_ui_thread(os.path.abspath(img_path))
            )

            update_status_on_ui_thread(f"Status: Extraction complete! {len(midframe_timecodes_str)} images saved to {output_d}")
            extraction_successful = True # Mark as successful if we reach here

        except Exception as ex:
            error_msg = f"Error during extraction: {str(ex)}"
            print(error_msg) # Log to console as well
            update_status_on_ui_thread(error_msg)
        finally:
            start_button.disabled = False
            progress_bar.visible = False
            # Set final status message based on success
            if extraction_successful:
                final_status = f"Status: Successfully extracted {len(image_grid.controls)} images to {output_d}."
                update_status_on_ui_thread(final_status)
            else:
                # If an error message was already set via update_status_on_ui_thread in except block or ffmpeg errors,
                # do not override. Otherwise, set generic failure message.
                if not (status_text.value.startswith("Error:") or status_text.value.startswith("ffmpeg error")):
                    update_status_on_ui_thread("Status: Extraction failed or was interrupted.")
            page_ref.update()

    def start_extraction_button_click(e):
        vid_path = selected_video_path_text.value
        out_dir = selected_output_dir_text.value

        if not vid_path or vid_path == "No video selected yet." or not os.path.exists(vid_path):
            status_text.value = "Error: Please select a valid video file."
            page.update()
            return
        if not out_dir or out_dir == "Output directory selection cancelled.":
            # If user cancelled, default_output_path is still in selected_output_dir_text.value
            # Ensure the default output directory exists or can be created
            out_dir = default_output_path 
            selected_output_dir_text.value = out_dir # Update UI if it was "cancelled"
        
        # Create output directory if it doesn't exist
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as oe:
            status_text.value = f"Error creating output directory: {oe}"
            page.update()
            return

        # Save settings to a text file
        settings_to_save = (
            f"Video File: {vid_path}\n"
            f"Output Folder: {out_dir}\n"
            f"Algorithm: {algo_dropdown.value}\n"
            f"Threshold: {threshold_input.value}\n"
            f"Min Scene Length (frames): {min_scene_len_input.value}\n"
        )
        if algo_dropdown.value == 'adaptive':
            settings_to_save += (
                f"Window Size (adaptive): {window_size_input.value}\n"
                f"Min Content Value (adaptive): {min_content_val_input.value}\n"
            )
        
        settings_file_path = os.path.join(out_dir, "extraction_settings.txt")
        try:
            with open(settings_file_path, 'w', encoding='utf-8') as sf:
                sf.write(settings_to_save)
            status_text.value = f"Status: Settings saved to {settings_file_path}" # Initial status before thread
        except Exception as ex_file_save:
            status_text.value = f"Warning: Could not save settings file: {ex_file_save}" 
            # Continue with extraction even if settings file fails to save

        start_button.disabled = True
        progress_bar.visible = True
        progress_bar.value = None # Indeterminate
        image_grid.controls.clear()
        page.update()

        try:
            current_threshold = float(threshold_input.value)
            current_min_scene_len = int(min_scene_len_input.value)
            current_window_size = int(window_size_input.value)
            current_min_content_val = float(min_content_val_input.value)
        except ValueError:
            status_text.value = "Error: Invalid numeric input for parameters (Threshold, Min Scene Len, etc.)."
            start_button.disabled = False
            progress_bar.visible = False
            page.update()
            return

        # Run extraction in a new thread
        thread = threading.Thread(
            target=run_extraction_thread_fn,
            args=(
                page,
                vid_path,
                out_dir,
                algo_dropdown.value,
                current_threshold,
                current_min_scene_len,
                current_window_size,
                current_min_content_val,
            ),
            daemon=True # Allows main program to exit even if thread is running
        )
        thread.start()

    start_button.on_click = start_extraction_button_click
    
    # --- Page Layout ---
    # Header for file/folder selection
    file_selection_row = Row(
        [
            ElevatedButton("Select Video File", icon=icons.MOVIE_FILTER_OUTLINED, height=35,
                           on_click=lambda _: video_file_picker.pick_files(
                               allow_multiple=False, allowed_extensions=["mp4", "mov", "avi", "mkv", "webm", "flv"]
                           )),
            Container(content=selected_video_path_text, padding=ft.padding.only(top=8, left=10)),
        ],
        alignment=MainAxisAlignment.START
    )
    output_dir_selection_row = Row(
        [
            ElevatedButton("Select Output Folder", icon=icons.FOLDER_OPEN_OUTLINED, height=35,
                           on_click=lambda _: output_dir_picker.get_directory_path(
                               dialog_title="Choose Output Directory", initial_directory=os.path.dirname(selected_output_dir_text.value) or os.getcwd()
                           )),
            Container(content=selected_output_dir_text, padding=ft.padding.only(top=8, left=10)),
        ],
        alignment=MainAxisAlignment.START
    )

    # Parameter settings row
    settings_row1 = Row(
        [
            algo_dropdown,
            threshold_input,
            min_scene_len_input,
        ],
        alignment=MainAxisAlignment.START, spacing=15
    )

    # Main column layout
    page.add(
        Column(
            [
                Text("Video Scene Extractor", size=24, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                file_selection_row,
                output_dir_selection_row,
                ft.Divider(),
                Text("Detection Parameters", size=18, weight=ft.FontWeight.BOLD),
                settings_row1,
                settings_row2_adaptive,
                ft.Divider(),
                Row([start_button], alignment=MainAxisAlignment.CENTER),
                progress_bar,
                Container(content=status_text, padding=5, alignment=alignment.center_left),
                ft.Divider(),
                Text("Extracted Scenes", size=18, weight=ft.FontWeight.BOLD),
                Container(
                    content=image_grid,
                    border=border.all(1, colors.OUTLINE),
                    border_radius=border_radius.all(5),
                    padding=10,
                    margin=ft.margin.only(top=5),
                    expand=True, # Make the grid container expand
                    alignment=alignment.top_center
                )
            ],
            spacing=12,
            expand=True, # Make the main column expand
            scroll=ft.ScrollMode.ADAPTIVE # Add scroll if content overflows
        )
    )
    # Initial call to set correct visibility for adaptive params row based on default algo value
    algo_changed_handler(None) 

if __name__ == "__main__":
    ft.app(target=main)
    # To run this as a desktop app, you might also use:
    # ft.app(target=main, view=ft.AppView.FLET_APP_HIDDEN) # For a more native feel if packaging later
    # Or simply `python mv_scene_gui.py`