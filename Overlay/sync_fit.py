import sys
import os
from pathlib import Path
import json
import cv2
import time
import fitdecode
import folium
import glob
import re
from datetime import datetime, timedelta
from PyQt5 import QtWidgets, QtGui, QtCore, QtWebEngineWidgets, QtWebChannel
from PyQt5.QtCore import QMetaObject, Qt
import ctypes
import ctypes.wintypes as wintypes
ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)

class JobObject:
    def __init__(self):
        self.kernel32 = ctypes.windll.kernel32

        # Create job object
        self.hJob = self.kernel32.CreateJobObjectW(None, None)

        # Configure job to kill all child processes when closed
        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        self.kernel32.SetInformationJobObject(
            self.hJob,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(info),
            ctypes.sizeof(info)
        )

    def assign(self, pid):
        PROCESS_ALL_ACCESS = 0x1F0FFF
        hProcess = self.kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        self.kernel32.AssignProcessToJobObject(self.hJob, hProcess)

class MapBridge(QtCore.QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self._parent = parent
    @QtCore.pyqtSlot()
    def mapIsReady(self):
        # Forward to SyncTool.mapIsReady()
        self._parent.mapIsReady()

class GroupBar(QtWidgets.QWidget):
    def __init__(self, groups, total_duration, parent=None):
        super().__init__(parent)
        self.groups = groups
        self.total = total_duration
        self.setMinimumHeight(12)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        w = self.width()
        h = self.height()

        x = 0
        for g in self.groups:
            dur = g["duration"]
            width = int((dur / self.total) * w)
            painter.fillRect(x, 0, width, h, QtGui.QColor(180, 180, 180))
            painter.setPen(QtGui.QColor(80, 80, 80))
            painter.drawRect(x, 0, width, h)
            x += width

class SyncTool(QtWidgets.QWidget):
    ui_stop_signal = QtCore.pyqtSignal()
    map_init_signal = QtCore.pyqtSignal()
    map_update_signal = QtCore.pyqtSignal(int)
    first_frame_signal = QtCore.pyqtSignal()
    eta_start_time = None
    eta_last_update = None
    eta_seconds_remaining = None

    def __init__(self, video_path, fit_path, parent=None):
        super().__init__(parent)
        self.video_file = Path(video_path).name

        # ---------------------------------------------------------
        # AUTO‑SCALING SETUP (DPI + SCREEN SIZE)
        # ---------------------------------------------------------
        screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
        dpi_scale = QtWidgets.QApplication.primaryScreen().logicalDotsPerInch() / 96.0

        self.screen_w = screen.width()
        self.screen_h = screen.height()
        self.user_dragging_fit = False
        # Target window height = 90% of screen height
        self.target_h = int(self.screen_h * 0.90)

        # Scale factor relative to 1080p baseline, adjusted for DPI
        self.scale_factor = (self.target_h / 1080.0) / dpi_scale

        # ---------------------------------------------------------
        # SIGNALS / STATE
        # ---------------------------------------------------------
        self.ui_stop_signal.connect(self._stop_overlay_ui)
        self.map_init_signal.connect(self._init_map_ui)
        self.map_update_signal.connect(self._update_map_ui)
        self.first_frame_signal.connect(self._draw_first_frame)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.next_frame)

        self.paused = True
        self.group_anchors = {}
        self.process = None
        self.video_path = video_path

        # ---------------------------------------------------------
        # LOAD FIT + METADATA
        # ---------------------------------------------------------
        self.fit_points = self.load_fit(fit_path)
        if not self.fit_points:
            raise SystemExit("No GPS points found in FIT")

        with open(META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)

        self.chapters = meta["chapters"]
        self.total_video_duration = meta["video"]["total_duration_sec"]

        # ---------------------------------------------------------
        # BUILD GOPRO GROUPS
        # ---------------------------------------------------------
        self.groups = []
        for ch in self.chapters:
            gkey = extract_group_key(ch["file"])
            dur = ch["duration_sec"]
            if not self.groups or self.groups[-1]["key"] != gkey:
                self.groups.append({"key": gkey, "duration": dur})
            else:
                self.groups[-1]["duration"] += dur

        # Group boundaries
        self.group_boundaries = []
        t = 0
        for g in self.groups:
            start = t
            end = t + g["duration"]
            self.group_boundaries.append((start, end))
            t = end

        self.sync_markers = []
        self.paused = False

        # ---------------------------------------------------------
        # AUTO‑SCALED VIDEO + MAP
        # ---------------------------------------------------------
        video_h = int(480 * self.scale_factor)
        video_w = int(video_h * (16/9))

        map_h = video_h
        map_w = int(600 * self.scale_factor)

        self.video_label = QtWidgets.QLabel()
        self.video_label.setFixedSize(video_w, video_h)

        self.map_view = QtWebEngineWidgets.QWebEngineView()
        self.map_view.setMinimumSize(map_w, map_h)

        hbox = QtWidgets.QHBoxLayout()
        hbox.addWidget(self.video_label)
        hbox.addWidget(self.map_view)

        # ---------------------------------------------------------
        # SLIDERS
        # ---------------------------------------------------------
        self.slider = MarkerSlider(QtCore.Qt.Horizontal)
        self.slider.valueChanged.connect(self.on_slider)

        #self.fit_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.fit_slider = MarkerSlider(QtCore.Qt.Horizontal)
        self.fit_slider.setMinimum(0)
        self.fit_slider.setMaximum(len(self.fit_points) - 1)
        self.fit_slider.valueChanged.connect(self.on_fit_slider)
        self.fit_slider.sliderPressed.connect(self.on_fit_slider_pressed)
        self.fit_slider.sliderReleased.connect(self.on_fit_slider_released)

        policy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                       QtWidgets.QSizePolicy.Fixed)
        self.slider.setSizePolicy(policy)
        self.fit_slider.setSizePolicy(policy)

        # ---------------------------------------------------------
        # PLAYBACK BUTTONS
        # ---------------------------------------------------------
        self.btn_play = QtWidgets.QPushButton()
        self.btn_play.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_MediaPlay))
        self.btn_play.setIconSize(QtCore.QSize(32, 32))
        self.btn_play.clicked.connect(self.toggle_play)

        self.btn_prev = QtWidgets.QPushButton()
        self.btn_prev.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_MediaSkipBackward))
        self.btn_prev.setIconSize(QtCore.QSize(28, 28))
        self.btn_prev.clicked.connect(self.prev_frame)

        self.btn_next = QtWidgets.QPushButton()
        self.btn_next.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_MediaSkipForward))
        self.btn_next.setIconSize(QtCore.QSize(28, 28))
        self.btn_next.clicked.connect(self.next_frame_manual)

        play_row = QtWidgets.QHBoxLayout()
        play_row.addStretch(1)
        play_row.addWidget(self.btn_prev)
        play_row.addWidget(self.btn_play)
        play_row.addWidget(self.btn_next)
        play_row.addStretch(1)

        # ---------------------------------------------------------
        # MARKER LIST (AUTO‑SCALED)
        # ---------------------------------------------------------
        marker_h = int(100 * self.scale_factor)

        self.marker_list = QtWidgets.QListWidget()
        self.marker_list.setMaximumHeight(marker_h)
        self.marker_list.itemDoubleClicked.connect(
            lambda item: self.jump_to_marker(self.marker_list.currentRow())
        )

        # ---------------------------------------------------------
        # LOG WINDOW (AUTO‑SCALED)
        # ---------------------------------------------------------
        log_min = int(120 * self.scale_factor)
        log_max = int(260 * self.scale_factor)

        self.log_window = QtWidgets.QPlainTextEdit()
        self.log_window.setReadOnly(True)
        self.log_window.setMinimumHeight(log_min)
        self.log_window.setMaximumHeight(log_max)
        self.log_window.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed
        )
        self.log_window.setStyleSheet(
            "background-color: black; color: lime; font-family: Consolas; font-size: 11pt;"
        )

        # ---------------------------------------------------------
        # ENCODER BUTTONS + PROGRESS
        # ---------------------------------------------------------
        self.btn_mark = QtWidgets.QPushButton("Drop Sync Marker")
        self.btn_mark.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_ArrowDown))
        self.btn_mark.clicked.connect(self.drop_marker)

        self.btn_save = QtWidgets.QPushButton("Save Markers")
        self.btn_save.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogSaveButton))
        self.btn_save.clicked.connect(self.save_markers)

        self.btn_delete = QtWidgets.QPushButton("Delete Selected Marker")
        self.btn_delete.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_TrashIcon))
        self.btn_delete.clicked.connect(self.confirm_delete_marker)
        self.btn_delete.setStyleSheet("background-color: #b00000; color: white; font-weight: bold;")

        self.btn_run_overlay = QtWidgets.QPushButton("Render Overlay (Run Encoder)")
        self.btn_run_overlay.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_MediaPlay))
        self.btn_run_overlay.setIconSize(QtCore.QSize(24, 24))
        self.btn_run_overlay.setStyleSheet("background-color: #007f00; color: white; font-weight: bold;")
        self.btn_run_overlay.clicked.connect(self.run_overlay_script)

        self.btn_stop_overlay = QtWidgets.QPushButton("Stop Encoding")
        self.btn_stop_overlay.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_BrowserStop))
        self.btn_stop_overlay.setIconSize(QtCore.QSize(24, 24))
        self.btn_stop_overlay.setStyleSheet("background-color: #444; color: white; font-weight: bold;")
        self.btn_stop_overlay.clicked.connect(self.stop_overlay_script)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%  (%v / %m)")
        self.progress_bar.setAlignment(QtCore.Qt.AlignCenter)
        #self.progress_bar.setFormat("Encoding Progress: %p%")

        # ---------------------------------------------------------
        # CONTROLS LAYOUT
        # ---------------------------------------------------------
        controls = QtWidgets.QVBoxLayout()
        controls.addWidget(QtWidgets.QLabel("Markers"))
        controls.addWidget(self.marker_list)

        marker_controls = QtWidgets.QHBoxLayout()
        marker_controls.addWidget(self.btn_mark)
        marker_controls.addWidget(self.btn_save)
        marker_controls.addStretch(1)
        marker_controls.addWidget(self.btn_delete)
        controls.addLayout(marker_controls)

        sliders_box = QtWidgets.QVBoxLayout()
        sliders_box.addWidget(QtWidgets.QLabel("Video"))
        sliders_box.addWidget(self.slider)
        sliders_box.addWidget(QtWidgets.QLabel("FIT"))
        sliders_box.addWidget(self.fit_slider)

        controls.addLayout(sliders_box)
        controls.addWidget(self.progress_bar)
        #controls.addWidget(QtWidgets.QLabel("Encoder Log"))
        controls.addWidget(self.log_window)
        controls.addWidget(self.btn_run_overlay)
        controls.addWidget(self.btn_stop_overlay)

        # ---------------------------------------------------------
        # FINAL LAYOUT
        # ---------------------------------------------------------
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(hbox)
        layout.addLayout(play_row)
        layout.addLayout(controls)

        # ---------------------------------------------------------
        # VIDEO SETUP (unchanged)
        # ---------------------------------------------------------
        self.cap = cv2.VideoCapture(self.video_path)
        if not self.cap.isOpened():
            raise SystemExit(f"Cannot open video: {self.video_path}")

        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.slider.setMaximum(max(self.total_frames - 1, 0))

        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        self.slider.blockSignals(True)
        self.slider.setValue(0)
        self.slider.blockSignals(False)

        fit_idx = self.video_time_to_fit_index(0.0)
        self.current_fit_index = fit_idx

        self.fit_slider.blockSignals(True)
        self.fit_slider.setValue(fit_idx)
        self.fit_slider.blockSignals(False)

        # ---------------------------------------------------------
        # MAP SETUP (unchanged)
        # ---------------------------------------------------------
        QtCore.QTimer.singleShot(0, self.map_init_signal.emit)

        self.map_ready = False
        self.channel = QtWebChannel.QWebChannel()
        self.map_bridge = MapBridge(self)
        self.channel.registerObject("qt_object", self.map_bridge)
        self.map_view.page().setWebChannel(self.channel)

        def on_html_loaded(ok):
            if ok:
                self.map_view.page().runJavaScript("""
                    window.pyReady = function() {
                        py.mapIsReady();
                    };
                    window.pyReady();
                """)

        self.map_view.loadFinished.connect(on_html_loaded)

        fit_idx = self.video_time_to_fit_index(0.0)
        self.current_fit_index = fit_idx
        self.map_update_signal.emit(fit_idx)

        self.paused = True
        self.btn_play.setText("Play")

        self.slider.set_markers([], self.total_video_duration, self.group_boundaries)

        self.current_fit_index = 0
        self.map_update_signal.emit(self.current_fit_index)

        # ---------------------------------------------------------
        # LOAD EXISTING MARKERS (if present AND video matches)
        # ---------------------------------------------------------
        markers_path = Path("sync_markers.json")
        if markers_path.exists():
            try:
                with markers_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)

                saved_video = data.get("video_file")
                saved_markers = data.get("markers", [])

                if saved_video != self.video_file:
                    print(f"Markers belong to different video ({saved_video}), ignoring.")
                    # Optional: auto-clear mismatched file
                    # markers_path.unlink()
                else:
                    self.sync_markers = saved_markers
                    print(f"Loaded {len(self.sync_markers)} markers for {saved_video}")
                    self.refresh_marker_list()

            except Exception as e:
                print("ERROR loading sync_markers.json:", e)

        # ---------------------------------------------------------
        # PLAYBACK TIMER + SHORTCUTS (unchanged)
        # ---------------------------------------------------------
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.next_frame)
        self.timer.stop()
        QtCore.QTimer.singleShot(0, self.first_frame_signal.emit)

        QtWidgets.QShortcut(QtGui.QKeySequence("Space"), self, activated=self.toggle_play)
        QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Left), self, activated=self.prev_frame)
        QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Right), self, activated=self.next_frame_manual)
        QtWidgets.QShortcut(QtGui.QKeySequence("Shift+Left"), self, activated=lambda: self.slider.setValue(max(0, self.slider.value() - 10)))
        QtWidgets.QShortcut(QtGui.QKeySequence("Shift+Right"), self, activated=lambda: self.slider.setValue(min(self.total_frames - 1, self.slider.value() + 10)))
    ### END INIT ####

    def run_on_ui_thread(self, fn):
        QMetaObject.invokeMethod(self, fn.__name__, Qt.QueuedConnection)

    @QtCore.pyqtSlot()
    def _init_map_ui(self):
        html_path = Path(__file__).with_name("map_template.html")
        self.map_view.load(QtCore.QUrl.fromLocalFile(str(html_path)))

    @QtCore.pyqtSlot()
    def _draw_first_frame(self):
        self.on_slider(0)

    @QtCore.pyqtSlot()
    def _stop_overlay_ui(self):
        if hasattr(self, "timer"):
            self.timer.stop()

        self.paused = True
        self.btn_play.setText("Play")
        self.btn_play.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_MediaPlay))

        self.btn_run_overlay.setEnabled(True)
        self.btn_stop_overlay.setEnabled(False)

    def _stop_encoder_process(self):
        if hasattr(self, "process") and self.process.state() == QtCore.QProcess.Running:
            pid = self.process.processId()

            # Log for debugging
            self.log_window.appendPlainText("Sending CTRL-BREAK to encoder…")

            import ctypes
            kernel32 = ctypes.windll.kernel32

            # ⭐ Send CTRL_BREAK_EVENT to the encoder's PROCESS GROUP
            #    This stops ffmpeg cleanly WITHOUT killing Python.
            kernel32.GenerateConsoleCtrlEvent(1, ffmpeg_pid)

            # ⭐ Wait for encoder script to exit normally
            if not self.process.waitForFinished(5000):
                self.log_window.appendPlainText("Encoder did not exit in time, killing…")
                self.process.kill()

            self.log_window.appendPlainText("Encoder stopped.")

    @QtCore.pyqtSlot(int)
    def _update_map_ui(self, fit_idx):
        self.update_map(fit_idx)

    @QtCore.pyqtSlot()
    def mapIsReady(self):
        self.map_ready = True

        # Draw full polyline
        coords = [[float(p[1]), float(p[2])] for p in self.fit_points]
        self.map_view.page().runJavaScript(f"setPolyline({coords});")

        # Determine the correct starting FIT index
        fit_idx = getattr(self, "current_fit_index", 0)

        # Move marker to the correct starting point
        lat = float(self.fit_points[fit_idx][1])
        lon = float(self.fit_points[fit_idx][2])
        self.map_view.page().runJavaScript(f"moveMarker({lat}, {lon});")

        # Optional: ensure map centers on the marker
        self.map_update_signal.emit(fit_idx)

    def closeEvent(self, event):
        if hasattr(self, "timer"):
            self.timer.stop()
        event.accept()

    def prev_frame(self):
        frame = max(0, self.slider.value() - 1)
        self.slider.setValue(frame)

    def next_frame_manual(self):
        frame = min(self.total_frames - 1, self.slider.value() + 1)
        self.slider.setValue(frame)

    def sync_slider_widths(self):
        # Ensure both sliders have identical width after layout is complete
        w = max(self.slider.width(), self.fit_slider.width())
        self.slider.setMinimumWidth(w)
        self.fit_slider.setMinimumWidth(w)

    def on_encoder_state_changed(self, state):
        if state == QtCore.QProcess.Starting:
            # Optional: show “starting…” state
            self.btn_run_overlay.setEnabled(False)
            self.btn_stop_overlay.setEnabled(False)

        elif state == QtCore.QProcess.Running:
            # Encoder is now fully running → update UI
            self.btn_run_overlay.setStyleSheet("background-color: #444; color: white; font-weight: bold;")
            self.btn_stop_overlay.setStyleSheet("background-color: #b00000; color: white; font-weight: bold;")
            self.btn_stop_overlay.setEnabled(True)
            self.btn_run_overlay.setEnabled(False)

        elif state == QtCore.QProcess.NotRunning:
            # Encoder finished or stopped
            self.btn_run_overlay.setStyleSheet("background-color: #007f00; color: white; font-weight: bold;")
            self.btn_stop_overlay.setStyleSheet("background-color: #444; color: white; font-weight: bold;")
            self.btn_stop_overlay.setEnabled(False)
            self.btn_run_overlay.setEnabled(True)

    def run_overlay_script(self):
        self.log_window.clear()
        self.progress_bar.setValue(0)

        self.process = QtCore.QProcess(self)
        self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.on_process_output)
        self.process.finished.connect(self.on_process_finished)

        # NEW: react to process state changes
        self.process.stateChanged.connect(self.on_encoder_state_changed)

        # Create job object
        self.job = JobObject()

        python = sys.executable
        args = ["cycling_overlay_mp4_direct.py"]

        self.log_window.appendPlainText("Starting encoder...\n")
        self.process.start(python, args)

        self.eta_start_time = time.time()
        self.eta_last_update = self.eta_start_time
        self.eta_seconds_remaining = None

        # Assign to job object
        pid = self.process.processId()
        self.job.assign(pid)

    def stop_overlay_script(self):
        pid = get_ffmpeg_pid()
        if pid:
            print(f"Sending CTRL_BREAK to ffmpeg PID {pid}")
            ctypes.windll.kernel32.GenerateConsoleCtrlEvent(1, pid)
        else:
            print("No ffmpeg PID found")
        os.remove("ffmpeg_pid.json")

    def on_process_output(self):
        data = self.process.readAll().data().decode("utf-8", errors="ignore")
        self.log_window.appendPlainText(data)

        # Auto-scroll
        sb = self.log_window.verticalScrollBar()
        sb.setValue(sb.maximum())

        # Try to parse ffmpeg progress
        self.parse_ffmpeg_progress(data)

    def parse_ffmpeg_progress(self, text):
        """
        Parse ffmpeg progress from stderr.
        Supports:
          time=HH:MM:SS.xx
          out_time=HH:MM:SS.xx
          out_time_ms=123456
        """

        # --- Try time=HH:MM:SS.xx ---
        m = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", text)
        if m:
            h, m_, s = m.groups()
            current_sec = int(h)*3600 + int(m_)*60 + float(s)
            return self.update_progress_bar(current_sec)

        # --- Try out_time=HH:MM:SS.xx ---
        m = re.search(r"out_time=(\d+):(\d+):(\d+\.\d+)", text)
        if m:
            h, m_, s = m.groups()
            current_sec = int(h)*3600 + int(m_)*60 + float(s)
            return self.update_progress_bar(current_sec)

        # --- Try out_time_ms=123456 ---
        m = re.search(r"out_time_ms=(\d+)", text)
        if m:
            ms = int(m.group(1))
            current_sec = ms / 1000.0
            return self.update_progress_bar(current_sec)

    def update_progress_bar(self, current_sec):
        total_sec = self.total_video_duration
        if total_sec <= 0:
            return

        # --- Percent complete ---
        pct = int((current_sec / total_sec) * 100)
        pct = max(0, min(100, pct))
        self.progress_bar.setValue(pct)

        # --- Initialize ETA timer ---
        if self.eta_start_time is None:
            self.eta_start_time = time.time()

        # --- Compute ETA ---
        elapsed = time.time() - self.eta_start_time
        progress_fraction = current_sec / total_sec

        if progress_fraction > 0:
            total_estimated = elapsed / progress_fraction
            remaining = total_estimated - elapsed
            self.eta_seconds_remaining = max(0, int(remaining))

            # Format ETA as HH:MM:SS
            h = self.eta_seconds_remaining // 3600
            m = (self.eta_seconds_remaining % 3600) // 60
            s = self.eta_seconds_remaining % 60
            eta_str = f"{h:02d}:{m:02d}:{s:02d}"
        else:
            eta_str = "--:--:--"

        # --- Update progress bar text ---
        self.progress_bar.setFormat(f"{pct}%  —  ETA {eta_str}")
        self.progress_bar.setAlignment(QtCore.Qt.AlignCenter)

    def on_process_finished(self, exit_code, exit_status):
        self.log_window.appendPlainText("\n--- PROCESS FINISHED ---")
        self.log_window.appendPlainText(f"Exit code: {exit_code}")
        self.btn_run_overlay.setStyleSheet("background-color: #007f00; color: white; font-weight: bold;")
        self.btn_stop_overlay.setStyleSheet("background-color: #444; color: white; font-weight: bold;")
        self.btn_stop_overlay.setEnabled(False)
        self.btn_run_overlay.setEnabled(True)
        self.btn_stop_overlay.setEnabled(False)

        # Reset progress bar
        self.progress_bar.setValue(100)

    def update_group_anchors(self):
        self.group_anchors = {}
        for m in self.sync_markers:
            g = int(m["group"])
            if g not in self.group_anchors:
                av = float(m["video_sec"])
                ats = datetime.fromisoformat(m["fit_timestamp"])
                self.group_anchors[g] = (av, ats)

    def jump_to_marker(self, row):
        if row < 0 or row >= len(self.sync_markers):
            return

        m = self.sync_markers[row]

        # Extract marker data
        video_sec = float(m["video_sec"])
        fit_idx = int(m["fit_index"])

        # --- Jump video ---
        frame_idx = int(video_sec * self.fps)
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)

        # Update video slider
        self.slider.blockSignals(True)
        self.slider.setValue(frame_idx)
        self.slider.blockSignals(False)

        # Force video preview update
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        self.cap.grab()
        ret, frame = self.cap.retrieve()

        if ret:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame_small = cv2.resize(frame_rgb, (960, 540), interpolation=cv2.INTER_AREA)
            h, w, ch = frame_small.shape
            img = QtGui.QImage(frame_small.data, w, h, ch * w, QtGui.QImage.Format_RGB888)
            self.video_label.setPixmap(QtGui.QPixmap.fromImage(img))

        # --- Jump FIT slider ---
        self.current_fit_index = fit_idx
        self.fit_slider.blockSignals(True)
        self.fit_slider.setValue(fit_idx)
        self.fit_slider.blockSignals(False)

        # --- Update map ---
        self.map_update_signal.emit(fit_idx)

        print(f"Jumped to marker {row}: video={video_sec:.2f}s, fit_idx={fit_idx}")

    def refresh_marker_list(self):
        current = self.marker_list.currentRow()

        self.marker_list.clear()
        for m in self.sync_markers:
            txt = f"Group {m['group']} | Video {m['video_sec']:.2f}s | FIT idx {m['fit_index']}"
            self.marker_list.addItem(txt)

        # Restore selection if possible
        if 0 <= current < len(self.sync_markers):
            self.marker_list.setCurrentRow(current)

        # Update slider ticks + chapter boundaries
        self.slider.set_markers(
            [m["video_sec"] for m in self.sync_markers],
            self.total_video_duration,
            self.group_boundaries
        )
        self.update_group_anchors()

    def confirm_delete_marker(self):
        row = self.marker_list.currentRow()
        if row < 0:
            return

        reply = QtWidgets.QMessageBox.question(
            self,
            "Delete Marker",
            "Are you sure you want to delete this marker?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )

        if reply == QtWidgets.QMessageBox.Yes:
            self.delete_marker()

    def delete_marker(self):
        row = self.marker_list.currentRow()
        if row < 0:
            print("No marker selected.")
            return

        removed = self.sync_markers.pop(row)
        print("Deleted marker:", removed)

        self.refresh_marker_list()

    # ---------------------------------------------------------
    # PLAY / PAUSE
    # ---------------------------------------------------------
    def toggle_play(self):
        # Flip state
        self.paused = not self.paused

        if self.paused:
            # PAUSE
            self.timer.stop()
            self.btn_play.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_MediaPlay))
            self.btn_play.setText("Play")
        else:
            # PLAY
            self.timer.setInterval(int(1000 / self.fps))
            self.timer.start()
            self.btn_play.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_MediaPause))
            self.btn_play.setText("Pause")

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Space:
            self.toggle_play()

    # ---------------------------------------------------------
    # FIT LOADING (Garmin semicircle conversion)
    # ---------------------------------------------------------
    def load_fit(self, path):
        pts = []
        SEMICIRCLE = 180.0 / (1 << 31)

        with fitdecode.FitReader(path) as fit:
            for frame in fit:
                if not isinstance(frame, fitdecode.records.FitDataMessage):
                    continue

                row = {f.name: f.value for f in frame.fields}
                ts = row.get("timestamp")
                lat_raw = row.get("position_lat")
                lon_raw = row.get("position_long")

                if ts is None or lat_raw is None or lon_raw is None:
                    continue

                if ts.tzinfo is not None:
                    ts = ts.astimezone().replace(tzinfo=None)

                lat = lat_raw * SEMICIRCLE
                lon = lon_raw * SEMICIRCLE

                pts.append((ts, lat, lon))

        pts.sort(key=lambda p: p[0])
        return pts

    # ---------------------------------------------------------
    # MAP (center on current marker)
    # ---------------------------------------------------------
    def build_map_html(self, marker_index: int) -> str:
        marker_index = max(0, min(marker_index, len(self.fit_points) - 1))

        lat0 = float(self.fit_points[marker_index][1])
        lon0 = float(self.fit_points[marker_index][2])

        # Base map
        m = folium.Map(location=[lat0, lon0], zoom_start=16, control_scale=True)

        # Polyline coordinates
        coords = [(float(p[1]), float(p[2])) for p in self.fit_points]
        poly = folium.PolyLine(coords, color="blue", weight=3, opacity=0.7)
        poly.add_to(m)

        # Initial marker
        lat_m, lon_m = coords[marker_index]
        marker = folium.CircleMarker(
            location=(lat_m, lon_m),
            radius=7,
            color="red",
            fill=True,
            fill_color="red",
            fill_opacity=1.0,
        )
        marker.add_to(m)

        # Inject JS for persistent marker + smooth movement
        m.get_root().html.add_child(folium.Element("""
    <script>
    var marker_ref = null;
    var poly_ref = null;
    var mapReady = false;

    map.whenReady(function() {
        mapReady = true;
        if (window.pyReady) {
            window.pyReady();
        }
    });

    function initObjects() {{
        // Polyline reference
        poly_ref = poly_{poly.get_name()};

        // Marker reference
        marker_ref = marker_{marker.get_name()};
    }}

    function registerPythonReady() {
        if (mapReady && window.pyReady) {
            window.pyReady();
        }
    }

    function moveMarker(lat, lng) {{
        if (marker_ref) {{
            marker_ref.setLatLng([lat, lng]);
            map.panTo([lat, lng], {{animate: true, duration: 0.25}});
        }}
    }}

    document.addEventListener("DOMContentLoaded", initObjects);
    </script>
    """))

        return m.get_root().render()

    def update_map(self, marker_index: int):
        if not self.map_ready:
            return  # map not ready yet

        marker_index = max(0, min(marker_index, len(self.fit_points) - 1))
        lat = float(self.fit_points[marker_index][1])
        lon = float(self.fit_points[marker_index][2])

        self.map_view.page().runJavaScript(f"moveMarker({lat}, {lon});")


    # ---------------------------------------------------------
    # VIDEO
    # ---------------------------------------------------------
    def next_frame(self):
        if self.paused:
            return

        now = time.time()

        # First tick: initialize clock
        if not hasattr(self, "play_clock"):
            self.play_clock = now
            self.video_clock = 0.0  # seconds of video elapsed
            self.last_frame_time = now

        # How much real time has passed?
        elapsed = now - self.last_frame_time
        self.last_frame_time = now

        # Advance video clock
        self.video_clock += elapsed

        # Compute which frame we *should* be on
        target_frame = int(self.video_clock * self.fps)

        # Current frame according to OpenCV
        current_frame = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))

        # If we're behind, skip frames
        if target_frame > current_frame:
            # Skip forward by reading and discarding frames
            skip = target_frame - current_frame
            for _ in range(skip):
                ret, _ = self.cap.read()
                if not ret:
                    self.timer.stop()
                    self.paused = True
                    return

        # Read the next frame normally
        ret, frame = self.cap.read()
        if not ret:
            self.timer.stop()
            self.paused = True
            return

        # --- Display frame ---
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame_small = cv2.resize(frame_rgb, (960, 540), interpolation=cv2.INTER_AREA)
        h, w, ch = frame_small.shape
        img = QtGui.QImage(frame_small.data, w, h, ch * w, QtGui.QImage.Format_RGB888)
        self.video_label.setPixmap(QtGui.QPixmap.fromImage(img))

        # --- Update slider ---
        frame_idx = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
        self.slider.blockSignals(True)
        self.slider.setValue(frame_idx)
        self.slider.blockSignals(False)

        # --- FIT mapping ---
        video_sec = frame_idx / self.fps
        fit_idx = self.video_time_to_fit_index(video_sec)

        if not self.user_dragging_fit and fit_idx != self.current_fit_index:
            self.current_fit_index = fit_idx
            self.fit_slider.blockSignals(True)
            self.fit_slider.setValue(fit_idx)
            self.fit_slider.blockSignals(False)
            self.map_update_signal.emit(fit_idx)


    def on_slider(self, frame_idx: int):
        # --- SAFETY: stop playback before seeking ---
        if not self.paused:
            self.timer.stop()
            self.paused = True
            self.btn_play.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_MediaPlay))
            self.btn_play.setText("Play")

        # Seek to frame
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)

        # --- Read frame ---
        ret, frame = self.cap.read()
        if not ret:
            print("WARNING: Could not read frame", frame_idx)
            return

        # --- Convert to QImage ---
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        qimg = QtGui.QImage(rgb.data, w, h, bytes_per_line, QtGui.QImage.Format_RGB888)

        # --- Display in preview widget ---
        pix = QtGui.QPixmap.fromImage(qimg)
        pix = pix.scaled(
            self.video_label.width(),
            self.video_label.height(),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation
        )
        self.video_label.setPixmap(pix)

        # --- FIT + map update ---
        video_sec = frame_idx / self.fps
        fit_idx = self.video_time_to_fit_index(video_sec)

        self.current_fit_index = fit_idx
        self.fit_slider.blockSignals(True)
        if not self.user_dragging_fit:
            self.fit_slider.blockSignals(True)
            self.fit_slider.setValue(fit_idx)
            self.fit_slider.blockSignals(False)
        self.fit_slider.blockSignals(False)

        self.map_update_signal.emit(fit_idx)

    # ---------------------------------------------------------
    # FIT SLIDER (manual marker control)
    # ---------------------------------------------------------
    def on_fit_slider(self, fit_idx):
        # --- SAFETY: stop playback before seeking ---
        if not self.paused:
            self.timer.stop()
            self.paused = True
            self.btn_play.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_MediaPlay))
            self.btn_play.setText("Play")

        # --- USER DRAGGING: update map live ---
        if self.user_dragging_fit:
            self.current_fit_index = fit_idx
            self.map_update_signal.emit(fit_idx)
            return

        # --- PROGRAMMATIC UPDATE: normal path ---
        self.current_fit_index = fit_idx
        self.map_update_signal.emit(fit_idx)


        if not self.user_dragging_fit:
            self.current_fit_index = fit_idx
            self.map_update_signal.emit(fit_idx)


    def on_fit_slider_pressed(self):
        self.user_dragging_fit = True

    def on_fit_slider_released(self):
        self.user_dragging_fit = False

    # ---------------------------------------------------------
    # SIMPLE VIDEO→FIT MAPPING (to be refined with markers)
    # ---------------------------------------------------------
    def video_time_to_fit_index(self, video_sec: float) -> int:
        # If we have per-group anchors, use zero-drift chapter mapping
        if self.group_anchors:
            g = self.get_current_group(video_sec)
            if g in self.group_anchors:
                anchor_video_sec, anchor_fit_ts = self.group_anchors[g]
                delta_sec = video_sec - anchor_video_sec
                target_ts = anchor_fit_ts + timedelta(seconds=delta_sec)
                # Find nearest FIT point to target_ts
                best_idx = 0
                best_err = float("inf")
                for i, (ts, _, _) in enumerate(self.fit_points):
                    err = abs((ts - target_ts).total_seconds())
                    if err < best_err:
                        best_err = err
                        best_idx = i
                return best_idx

        # Fallback: global linear mapping
        t0 = self.fit_points[0][0]
        t1 = self.fit_points[-1][0]
        fit_duration = (t1 - t0).total_seconds()
        if fit_duration <= 0:
            return 0
        frac = max(0.0, min(1.0, video_sec / fit_duration))
        idx = int(frac * (len(self.fit_points) - 1))
        return idx

    # ---------------------------------------------------------
    # GROUP HELPERS
    # ---------------------------------------------------------
    def get_current_group(self, video_sec: float) -> int:
        for i, (start, end) in enumerate(self.group_boundaries):
            if start <= video_sec < end:
                return i
        return len(self.group_boundaries) - 1

    # ---------------------------------------------------------
    # SYNC MARKERS (group-aware)
    # ---------------------------------------------------------
    def drop_marker(self):
        frame_idx = self.slider.value()
        video_sec = frame_idx / self.fps
        fit_idx = self.fit_slider.value()
        fit_ts = self.fit_points[fit_idx][0]
        group = self.get_current_group(video_sec)
        marker = {
            "group": int(group),
            "group_key": self.groups[group]["key"],
            "video_sec": float(video_sec),
            "fit_index": int(fit_idx),
            "fit_timestamp": fit_ts.isoformat(),
        }
        self.sync_markers.append(marker)
        self.refresh_marker_list()
        self.slider.set_markers(
            [m["video_sec"] for m in self.sync_markers],
            self.total_video_duration,
            self.group_boundaries
        )
        print("Added marker:", marker)

    def save_markers(self):
        if not self.sync_markers:
            print("No markers to save.")
            return

        out_path = Path("sync_markers.json")
        payload = {
            "video_file": self.video_file,   # <-- store the actual video filename
            "markers": self.sync_markers
        }

        with out_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        self.slider.set_markers(
            [m["video_sec"] for m in self.sync_markers],
            self.total_video_duration,
            self.group_boundaries
        )

        print(f"Saved {len(self.sync_markers)} markers for video:", self.video_file)

class MarkerSlider(QtWidgets.QSlider):
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.markers = []              # list of video_sec values
        self.total_duration = 1.0
        self.chapter_boundaries = []   # list of (start_sec, end_sec)

    def set_markers(self, markers, total_duration, chapter_boundaries=None):
        self.markers = markers
        self.total_duration = max(total_duration, 0.001)
        if chapter_boundaries is not None:
            self.chapter_boundaries = chapter_boundaries
        self.update()

    def wheelEvent(self, event):
        mods = QtWidgets.QApplication.keyboardModifiers()

        if mods & QtCore.Qt.ShiftModifier:
            step = 1      # ultra fine
        elif mods & QtCore.Qt.ControlModifier:
            step = 30     # coarse
        else:
            step = 10      # normal

        delta = event.angleDelta().y()
        if delta > 0:
            self.setValue(self.value() + step)
        else:
            self.setValue(self.value() - step)

        event.accept()

    # ⭐ CLICK-TO-JUMP BEHAVIOR
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            x = event.pos().x()
            w = self.width()
            ratio = x / w
            new_value = int(ratio * (self.maximum() - self.minimum())) + self.minimum()
            # Set slider position
            self.setValue(new_value)
            # ⭐ Force parent SyncTool to update video + map
            if hasattr(self.parent(), "on_slider"):
                self.parent().on_slider(new_value)
            event.accept()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        w = self.width()
        h = self.height()
        # Alternating chapter shading
        for i, (start, end) in enumerate(self.chapter_boundaries):
            x1 = int((start / self.total_duration) * w)
            x2 = int((end   / self.total_duration) * w)
            if i % 2 == 0:
                painter.fillRect(x1, 0, x2 - x1, h, QtGui.QColor(230, 230, 230, 60))
        # Chapter boundary lines
        painter.setPen(QtGui.QPen(QtGui.QColor(80, 80, 80), 1))
        for (start, end) in self.chapter_boundaries:
            x = int((start / self.total_duration) * w)
            painter.drawLine(x, 0, x, h)
        # Sync marker ticks
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 0, 0), 2))
        for sec in self.markers:
            x = int((sec / self.total_duration) * w)
            painter.drawLine(x, 0, x, h)

### END CLASSES ###

def get_ffmpeg_pid():
    try:
        pid_data = json.loads(PID_FILE.read_text())
        return pid_data.get("pid")
        pid_file = OVERLAY_DIR / "ffmpeg_pid.json"
        with pid_file.open("w") as f:
            data = json.load(f)
            return data.get("pid")
    except Exception:
        return None

def auto_detect_files():
    # 1. Detect video (exclude overlay outputs)
    videos = [
        str(p) for p in sorted(TODAY_DIR.glob("combined-*.mp4"))
        if "overlay" not in p.name.lower()
    ]

    if not videos:
        print("ERROR: No valid combined-*.mp4 file found (non-overlay).")
        sys.exit(1)

    VIDEO_PATH = videos[0]
    base = os.path.splitext(os.path.basename(VIDEO_PATH))[0]

    # 2. Detect JSON loosely matching the video name
    json_candidates = [str(p) for p in TODAY_DIR.glob("*.json")]

    # Prefer exact match first
    exact = [j for j in json_candidates if Path(j).stem.startswith(base)]
    if exact:
        META_PATH = exact[0]
    else:
        # Fuzzy match: JSON contains the base video name
        fuzzy = [j for j in json_candidates if base in Path(j).stem]
        if fuzzy:
            META_PATH = fuzzy[0]
        else:
            print("ERROR: No JSON metadata file similar to video name found.")
            print(f"Expected something containing: {base}")
            sys.exit(1)

    # 3. Detect FIT file
    fits = [str(p) for p in sorted(TODAY_DIR.glob("*.fit"))]
    if not fits:
        print("ERROR: No FIT file found.")
        sys.exit(1)

    FIT_PATH = fits[0]

    return VIDEO_PATH, META_PATH, FIT_PATH

def extract_group_key(filename: str) -> str:
    # filename like "2026-06-16-06-02-29-GX011102.MP4"
    base = filename.split("-")[-1]      # "GX011102.MP4"
    core = base.split(".")[0]           # "GX011102"
    return core[-4:]                    # "1102"

def build_groups(chapters):
    groups = []
    for ch in chapters:
        gkey = extract_group_key(ch["file"])
        dur = ch["duration_sec"]
        if not groups or groups[-1]["key"] != gkey:
            groups.append({"key": gkey, "duration": dur})
        else:
            groups[-1]["duration"] += dur

    boundaries = []
    t = 0.0
    for g in groups:
        start = t
        end = t + g["duration"]
        boundaries.append((start, end))
        t = end

    return groups, boundaries

def build_group_mapping(meta, markers):
    chapters = meta["chapters"]
    total_duration = meta["video"]["total_duration_sec"]

    groups, boundaries = build_groups(chapters)

    markers_by_group = {}
    for m in markers:
        g = int(m["group"])
        markers_by_group.setdefault(g, []).append(m)

    mapping = {
        "total_video_duration_sec": total_duration,
        "groups": []
    }

    for gi, g in enumerate(groups):
        start_sec, end_sec = boundaries[gi]

        if gi not in markers_by_group:
            print(f"WARNING: no marker for group {gi} (key {g['key']})")
            continue

        anchor = markers_by_group[gi][0]

        mapping["groups"].append({
            "group_index": gi,
            "group_key": g["key"],
            "video_start_sec": start_sec,
            "video_end_sec": end_sec,
            "anchor_video_sec": anchor["video_sec"],
            "anchor_fit_index": anchor["fit_index"],
            "anchor_fit_timestamp": anchor["fit_timestamp"],
        })

    return mapping

# -------------------------------------------------------------
# MAIN
# -------------------------------------------------------------
if __name__ == "__main__":
    TODAY_DIR = Path(r"D:\GoPro\Today")
    OVERLAY_DIR = Path(r"D:\Users\dylix\source\repos\GoPro\Overlay")
    PID_FILE = Path(__file__).resolve().parent / "ffmpeg_pid.json"
    VIDEO_PATH, META_PATH, FIT_PATH = auto_detect_files()
    print("Using video:", VIDEO_PATH)
    print("Using meta:", META_PATH)
    print("Using FIT:", FIT_PATH)

    # ---------------------------------------------------------
    # 2. LOAD META AND BUILD GROUP MAPPING
    # ---------------------------------------------------------
    with open(META_PATH, "r", encoding="utf-8") as f:
        meta = json.load(f)

    # Build groups + boundaries
    groups, boundaries = build_groups(meta["chapters"])

    # Write group_mapping.json (no markers yet)
    mapping = {
        "total_video_duration_sec": meta["video"]["total_duration_sec"],
        "groups": []
    }

    for gi, g in enumerate(groups):
        start_sec, end_sec = boundaries[gi]
        mapping["groups"].append({
            "group_index": gi,
            "group_key": g["key"],
            "video_start_sec": start_sec,
            "video_end_sec": end_sec
        })

    with open("group_mapping.json", "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2)

    print("Wrote initial group_mapping.json")

    # ---------------------------------------------------------
    # 3. LAUNCH GUI
    # ---------------------------------------------------------
    app = QtWidgets.QApplication(sys.argv)
    w = SyncTool(VIDEO_PATH, FIT_PATH)

    # First: resize to something that fits 1080p
    screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
    w.resize(int(screen.width() * 0.9), int(screen.height() * 0.9))

    # Then: center it
    def center_on_screen(win):
        screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
        size = win.frameGeometry()
        win.move(
            (screen.width() - size.width()) // 2,
            (screen.height() - size.height()) // 2
        )
    w.resize(1600, 900)
    center_on_screen(w)

    w.setWindowTitle("FIT ↔ Video Sync Tool (GoPro Group Sync)")
    w.show()
    sys.exit(app.exec_())