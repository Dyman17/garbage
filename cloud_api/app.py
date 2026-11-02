from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import cv2
import numpy as np
from fastapi import File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import gradio as gr
try:
    import spaces
    HAS_SPACES = True
except ImportError:
    HAS_SPACES = False

from classifier_mapping import UNKNOWN, resolve_bin
from detector import WasteDetector

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "servo_settings.json"
MODELS_DIR = BASE_DIR / "models"


def find_model(filename: str) -> str:
    in_sub = MODELS_DIR / filename
    if in_sub.is_file():
        return str(in_sub)
    in_root = BASE_DIR / filename
    if in_root.is_file():
        return str(in_root)
    return str(in_sub)


WASTE_MODEL_PATH = find_model("waste_yolov8s_best.pt")
FACE_MODEL_PATH = find_model("yolov8n-face.pt")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("herbibios.cloud_api")

# ----------------- Configuration & State -----------------
DEFAULT_SETTINGS: Dict[str, Any] = {
    "BIN_1": {"closed": 0, "open": 90},
    "BIN_2": {"closed": 0, "open": 90},
    "BIN_3": {"closed": 0, "open": 90},
    "hold_time_ms": 3000,
    "confidence_threshold": 0.55,
}


def load_settings() -> Dict[str, Any]:
    if SETTINGS_FILE.is_file():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                res = DEFAULT_SETTINGS.copy()
                res.update(saved)
                return res
        except Exception as exc:
            logger.warning("Could not read settings file: %s", exc)
    return DEFAULT_SETTINGS.copy()


def save_settings_to_disk(data: Dict[str, Any]) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        logger.error("Failed to save settings: %s", exc)


class State:
    def __init__(self) -> None:
        self.settings: Dict[str, Any] = load_settings()
        self.detector: Optional[WasteDetector] = None
        self.start_time: float = time.time()
        self.esp32_last_seen: float = 0.0
        self.last_annotated_frame_bgr: Optional[np.ndarray] = None
        self.last_annotated_jpeg: Optional[bytes] = None
        self.last_prediction: Dict[str, Any] = {
            "detected": False,
            "class_name": "Нет объектов",
            "confidence": 0.0,
            "bin": "-",
            "time": 0.0,
        }
        self.stats: Dict[str, int] = {"BIN_1": 0, "BIN_2": 0, "BIN_3": 0, "total": 0}
        self.item_latched: bool = False
        self.empty_frames: int = 0
        self.pending_manual_command: Optional[Dict[str, Any]] = None


state = State()

# Initialize models
try:
    logger.info("Loading WasteDetector models...")
    state.detector = WasteDetector(
        model_path=WASTE_MODEL_PATH,
        device="cpu",
        conf_threshold=state.settings.get("confidence_threshold", 0.55),
        face_model_path=FACE_MODEL_PATH,
    )
    logger.info("Models loaded successfully.")
except Exception as exc:
    logger.exception("Failed to initialize models: %s", exc)


# GPU Inference Wrapper for Hugging Face ZeroGPU
if HAS_SPACES:
    @spaces.GPU(duration=15)
    def run_inference_gpu(frame: np.ndarray):
        if state.detector is None:
            return []
        return state.detector.predict_frame(frame)
else:
    def run_inference_gpu(frame: np.ndarray):
        if state.detector is None:
            return []
        return state.detector.predict_frame(frame)


# ----------------- Gradio UI (Native Components = 100% Reliability) -----------------
theme = gr.themes.Base(
    primary_hue="emerald",
    neutral_hue="slate",
).set(
    body_background_fill="#020617",
    body_background_fill_dark="#020617",
    block_background_fill="#0f172a",
    block_background_fill_dark="#0f172a",
)

with gr.Blocks(title="HerbiBioS Waste AI", theme=theme, css="footer {display: none !important;}") as demo:
    gr.Markdown(
        """
        # ♻️ HerbiBioS — Cloud Waste AI Station
        **Автоматическая сортировочная станция (Облачный ИИ + ESP32-CAM)**
        """
    )

    with gr.Row():
        status_ai = gr.Textbox(
            value="🟢 Online (GPU Nvidia A100)" if HAS_SPACES else "🟢 Online (CPU)",
            label="Статус ИИ Облака",
            interactive=False,
            scale=1,
        )
        status_esp = gr.Textbox(
            value="🔴 Ожидание камеры (ESP32 не подключен)",
            label="Статус ESP32-CAM",
            interactive=False,
            scale=1,
        )
        last_item_text = gr.Textbox(
            value="Ожидание объектов...",
            label="Последний мусор в кадре",
            interactive=False,
            scale=2,
        )

    with gr.Row():
        # LEFT COLUMN: Live Camera View
        with gr.Column(scale=5):
            gr.Markdown("### 📷 Живой кадр с камеры ESP32-CAM")
            cam_image = gr.Image(
                value=None,
                label="Распознавание в реальном времени",
                type="numpy",
                interactive=False,
            )
            with gr.Row():
                stat_bin1 = gr.Number(value=0, label="🥤 БАК 1 (Пластик)", interactive=False)
                stat_bin2 = gr.Number(value=0, label="🥫 БАК 2 (Металл)", interactive=False)
                stat_bin3 = gr.Number(value=0, label="📦 БАК 3 (Бумага)", interactive=False)

        # RIGHT COLUMN: Servo Controls & Calibration
        with gr.Column(scale=5):
            gr.Markdown("### ⚡ Быстрое открытие створок (на 3 сек)")
            with gr.Row():
                btn_open_b1 = gr.Button("🥤 Открыть БАК 1", variant="primary")
                btn_open_b2 = gr.Button("🥫 Открыть БАК 2", variant="secondary")
                btn_open_b3 = gr.Button("📦 Открыть БАК 3", variant="secondary")

            gr.Markdown("### 🔧 Калибровка углов сервоприводов (0°–180°)")
            with gr.Group():
                gr.Markdown("**БАК 1 (Пластик — GPIO 12)**")
                with gr.Row():
                    s_b1_closed = gr.Slider(0, 180, value=state.settings["BIN_1"]["closed"], step=1, label="Закрыто (°)")
                    s_b1_open = gr.Slider(0, 180, value=state.settings["BIN_1"]["open"], step=1, label="Открыто (°)")
                    btn_test_b1 = gr.Button("Тест угла", size="sm")

            with gr.Group():
                gr.Markdown("**БАК 2 (Металл — GPIO 13)**")
                with gr.Row():
                    s_b2_closed = gr.Slider(0, 180, value=state.settings["BIN_2"]["closed"], step=1, label="Закрыто (°)")
                    s_b2_open = gr.Slider(0, 180, value=state.settings["BIN_2"]["open"], step=1, label="Открыто (°)")
                    btn_test_b2 = gr.Button("Тест угла", size="sm")

            with gr.Group():
                gr.Markdown("**БАК 3 (Бумага — GPIO 14)**")
                with gr.Row():
                    s_b3_closed = gr.Slider(0, 180, value=state.settings["BIN_3"]["closed"], step=1, label="Закрыто (°)")
                    s_b3_open = gr.Slider(0, 180, value=state.settings["BIN_3"]["open"], step=1, label="Открыто (°)")
                    btn_test_b3 = gr.Button("Тест угла", size="sm")

            s_hold_time = gr.Slider(1000, 8000, value=state.settings.get("hold_time_ms", 3000), step=500, label="Время открытия створки (мс)")
            btn_save = gr.Button("💾 Сохранить настройки углов в Облако", variant="primary", size="lg")

    # ----------------- SECTION: Manual AI Photo Tester -----------------
    gr.Markdown("---")
    with gr.Accordion("🧪 Тестирование ИИ (Загрузка любого фото для проверки работы моделей)", open=True):
        gr.Markdown(
            "Загрузите фото мусора с телефона или компьютера, чтобы проверить точность работы нейросетей "
            "(Waste YOLOv8s + Face Filter) без включения ESP32-CAM."
        )
        with gr.Row():
            with gr.Column(scale=5):
                test_upload = gr.Image(label="Загрузите тестовое изображение", type="numpy")
                btn_test_ai = gr.Button("🔍 Проанализировать фото через ИИ", variant="primary", size="lg")
            with gr.Column(scale=5):
                test_annotated = gr.Image(label="Результат детекции с рамками")
                test_report = gr.Markdown(value="*Загрузите фото и нажмите кнопку анализа...*")

    # Required hidden event trigger to satisfy Hugging Face ZeroGPU startup scanner
    dummy_input = gr.Image(visible=False)
    dummy_output = gr.JSON(visible=False)
    dummy_btn = gr.Button("Trigger GPU", visible=False)
    dummy_btn.click(fn=run_inference_gpu, inputs=dummy_input, outputs=dummy_output)

    # ----------------- UI Python Handlers -----------------
    def on_click_open(bin_name: str):
        cfg = state.settings.get(bin_name, {"closed": 0, "open": 90})
        state.pending_manual_command = {
            "type": "timed_open",
            "bin": bin_name,
            "open_angle": cfg["open"],
            "closed_angle": cfg["closed"],
            "hold_ms": state.settings.get("hold_time_ms", 3000),
        }
        logger.info("Manual open triggered for %s", bin_name)
        gr.Info(f"Команда: открыть {bin_name} на 3 сек отправлена в очередь!")

    btn_open_b1.click(fn=lambda: on_click_open("BIN_1"))
    btn_open_b2.click(fn=lambda: on_click_open("BIN_2"))
    btn_open_b3.click(fn=lambda: on_click_open("BIN_3"))

    def on_test_angle(bin_name: str, target_angle: int):
        state.pending_manual_command = {
            "type": "custom",
            "bin": bin_name,
            "angle": int(target_angle),
        }
        gr.Info(f"Тест {bin_name}: угол {target_angle}° отправлен!")

    btn_test_b1.click(fn=lambda a: on_test_angle("BIN_1", a), inputs=[s_b1_open])
    btn_test_b2.click(fn=lambda a: on_test_angle("BIN_2", a), inputs=[s_b2_open])
    btn_test_b3.click(fn=lambda a: on_test_angle("BIN_3", a), inputs=[s_b3_open])

    def on_save_settings(b1_c, b1_o, b2_c, b2_o, b3_c, b3_o, hold):
        state.settings["BIN_1"] = {"closed": int(b1_c), "open": int(b1_o)}
        state.settings["BIN_2"] = {"closed": int(b2_c), "open": int(b2_o)}
        state.settings["BIN_3"] = {"closed": int(b3_c), "open": int(b3_o)}
        state.settings["hold_time_ms"] = int(hold)
        save_settings_to_disk(state.settings)
        gr.Info("✅ Настройки углов сохранены в облаке!")
        return (
            int(b1_c), int(b1_o),
            int(b2_c), int(b2_o),
            int(b3_c), int(b3_o),
            int(hold),
        )

    btn_save.click(
        fn=on_save_settings,
        inputs=[s_b1_closed, s_b1_open, s_b2_closed, s_b2_open, s_b3_closed, s_b3_open, s_hold_time],
        outputs=[s_b1_closed, s_b1_open, s_b2_closed, s_b2_open, s_b3_closed, s_b3_open, s_hold_time],
    )

    # Automatically load saved settings on every page load/refresh
    def on_load_settings():
        s = load_settings()
        return (
            s["BIN_1"]["closed"],
            s["BIN_1"]["open"],
            s["BIN_2"]["closed"],
            s["BIN_2"]["open"],
            s["BIN_3"]["closed"],
            s["BIN_3"]["open"],
            s.get("hold_time_ms", 3000),
        )

    demo.load(
        fn=on_load_settings,
        outputs=[s_b1_closed, s_b1_open, s_b2_closed, s_b2_open, s_b3_closed, s_b3_open, s_hold_time],
    )

    # Handler for manual AI photo testing
    def analyze_manual_test_image(img):
        if img is None:
            return None, "⚠️ Пожалуйста, загрузите изображение."
        if state.detector is None:
            return None, "❌ Модель не инициализирована."

        frame_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        detections = run_inference_gpu(frame_bgr)
        annotated_bgr = state.detector.draw_detections(frame_bgr, detections)
        annotated_rgb = cv2.cvtColor(annotated_bgr, cv2.COLOR_BGR2RGB)

        waste_items = [d for d in detections if not d.is_face]
        faces = [d for d in detections if d.is_face]

        lines = ["### 📊 Отчёт о распознавании:"]
        if faces:
            lines.append(f"- 👤 **Лица в кадре:** обнаружено {len(faces)} (отсечены Face Filter)")
        else:
            lines.append("- 👤 **Лица в кадре:** не обнаружены")

        if waste_items:
            best = max(waste_items, key=lambda item: item.confidence)
            target_bin = resolve_bin(best.class_name)
            bin_desc = {
                "BIN_1": "🥤 БАК 1 (Пластик / Тетрапак)",
                "BIN_2": "🥫 БАК 2 (Металл / Алюминиевые банки)",
                "BIN_3": "📦 БАК 3 (Бумага / Картон)",
            }.get(target_bin, "❓ UNKNOWN (Не сортируемый объект)")

            lines.append(f"- 🎯 **Главный объект:** `{best.class_name}` (уверенность: **{round(best.confidence*100, 1)}%**)")
            lines.append(f"- 🚪 **Решение урны:** **{bin_desc}**")
            lines.append("\n**Все обнаруженные объекты на фото:**")
            for i, d in enumerate(waste_items, 1):
                b_name = resolve_bin(d.class_name)
                lines.append(f"{i}. `{d.class_name}` — {round(d.confidence*100, 1)}% ➔ **{b_name}**")
        else:
            lines.append("- ❌ **Мусор не обнаружен.** Створки урны останутся закрытыми.")

        return annotated_rgb, "\n".join(lines)

    btn_test_ai.click(
        fn=analyze_manual_test_image,
        inputs=[test_upload],
        outputs=[test_annotated, test_report],
    )

    # Auto refresh state every 2 seconds
    def update_dashboard():
        now = time.time()
        esp_online = (now - state.esp32_last_seen) < 8.0 if state.esp32_last_seen > 0 else False
        if esp_online:
            esp_text = f"🟢 Online (кадр {round(now - state.esp32_last_seen, 1)}с назад)"
        elif state.esp32_last_seen > 0:
            esp_text = f"🔴 Не в сети ({round(now - state.esp32_last_seen, 1)}с назад)"
        else:
            esp_text = "🔴 Ожидание камеры (ESP32 не подключен)"

        pred = state.last_prediction
        if pred.get("detected"):
            pred_text = f"{pred.get('class_name')} ({round(pred.get('confidence', 0)*100)}%) ➔ {pred.get('bin')}"
        else:
            pred_text = "В кадре чисто (нет мусора)"

        # Convert BGR to RGB for Gradio display
        rgb_frame = None
        if state.last_annotated_frame_bgr is not None:
            rgb_frame = cv2.cvtColor(state.last_annotated_frame_bgr, cv2.COLOR_BGR2RGB)

        return (
            esp_text,
            pred_text,
            rgb_frame,
            state.stats.get("BIN_1", 0),
            state.stats.get("BIN_2", 0),
            state.stats.get("BIN_3", 0),
        )

    refresh_timer = gr.Timer(2.0)
    refresh_timer.tick(
        fn=update_dashboard,
        outputs=[status_esp, last_item_text, cam_image, stat_bin1, stat_bin2, stat_bin3],
    )


# ----------------- Custom API Endpoints for ESP32-CAM & Dashboard -----------------
def health_check():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - state.start_time),
        "models_loaded": state.detector is not None,
        "zerogpu_enabled": HAS_SPACES,
    }


def get_system_status():
    now = time.time()
    esp32_online = (now - state.esp32_last_seen) < 8.0 if state.esp32_last_seen > 0 else False
    seconds_ago = round(now - state.esp32_last_seen, 1) if state.esp32_last_seen > 0 else None

    return {
        "hf_status": "online",
        "esp32_status": "online" if esp32_online else "offline",
        "esp32_last_seen_sec": seconds_ago,
        "settings": state.settings,
        "last_prediction": state.last_prediction,
        "stats": state.stats,
        "has_frame": state.last_annotated_frame_bgr is not None,
        "item_latched": state.item_latched,
    }


def get_latest_frame():
    if state.last_annotated_jpeg is None:
        raise HTTPException(status_code=404, detail="No camera frame available yet")
    return Response(content=state.last_annotated_jpeg, media_type="image/jpeg")


# Main frame endpoint called by ESP32-CAM (POST /frame)
async def process_frame(
    request: Request,
    image: Optional[UploadFile] = File(None),
):
    now = time.time()
    state.esp32_last_seen = now

    # 1. Check if there is an urgent pending manual command from the website
    manual_cmd = None
    if state.pending_manual_command is not None:
        manual_cmd = state.pending_manual_command
        state.pending_manual_command = None
        logger.info("Dispatching manual command to ESP32: %s", manual_cmd)

    # 2. Extract image bytes
    image_bytes = None
    if image is not None:
        image_bytes = await image.read()
    else:
        content_type = request.headers.get("content-type", "")
        if "image" in content_type or "octet-stream" in content_type:
            image_bytes = await request.body()

    if not image_bytes:
        return {
            "status": "ack",
            "command": manual_cmd,
            "server_time": now,
        }

    # 3. Decode frame with OpenCV
    np_arr = np.frombuffer(image_bytes, np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if frame is None:
        return {"status": "error", "message": "Could not decode JPEG image", "command": manual_cmd}

    if state.detector is None:
        return {"status": "error", "message": "Detector not ready", "command": manual_cmd}

    # 4. Run GPU-accelerated inference
    detections = run_inference_gpu(frame)
    waste_items = [d for d in detections if not d.is_face]
    face_detected = any(d.is_face for d in detections)

    # 5. Draw bounding boxes
    annotated = state.detector.draw_detections(frame, detections)
    state.last_annotated_frame_bgr = annotated

    ok, encoded = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if ok:
        state.last_annotated_jpeg = encoded.tobytes()

    # 6. Sorting logic
    auto_command = None

    if waste_items:
        state.empty_frames = 0
        best = max(waste_items, key=lambda item: item.confidence)
        target_bin = resolve_bin(best.class_name)

        state.last_prediction = {
            "detected": True,
            "class_name": best.class_name,
            "confidence": round(float(best.confidence), 3),
            "bin": target_bin,
            "face_detected": face_detected,
            "time": now,
        }

        if target_bin != UNKNOWN and not state.item_latched:
            state.item_latched = True
            state.stats[target_bin] = state.stats.get(target_bin, 0) + 1
            state.stats["total"] += 1

            bin_cfg = state.settings.get(target_bin, {"closed": 0, "open": 90})
            auto_command = {
                "type": "timed_open",
                "bin": target_bin,
                "open_angle": bin_cfg["open"],
                "closed_angle": bin_cfg["closed"],
                "hold_ms": state.settings.get("hold_time_ms", 3000),
            }
            logger.info("Auto-sort trigger: %s (%.2f) -> %s", best.class_name, best.confidence, target_bin)
    else:
        state.empty_frames += 1
        if state.item_latched and state.empty_frames >= 2:
            state.item_latched = False
            logger.info("Scene cleared; sorting logic re-armed.")

        state.last_prediction = {
            "detected": False,
            "class_name": "Нет объектов",
            "confidence": 0.0,
            "bin": "-",
            "face_detected": face_detected,
            "time": now,
        }

    final_command = manual_cmd or auto_command

    return {
        "status": "ok",
        "command": final_command,
        "latched": state.item_latched,
        "prediction": state.last_prediction,
    }


# Server Entry Point for Hugging Face Spaces & Local Execution
if __name__ == "__main__":
    from fastapi.routing import APIRoute

    # Launch Gradio without blocking thread so we can prepend our routes
    server_app, local_url, share_url = demo.launch(prevent_thread_lock=True)

    # Prepend custom endpoints to the top of router routes so they take precedence over SvelteKit catch-all
    custom_routes = [
        APIRoute("/frame", process_frame, methods=["POST"]),
        APIRoute("/api/frame", process_frame, methods=["POST"]),
        APIRoute("/esp32_frame", process_frame, methods=["POST"]),
        APIRoute("/health", health_check, methods=["GET"]),
        APIRoute("/api/health", health_check, methods=["GET"]),
        APIRoute("/status", get_system_status, methods=["GET"]),
        APIRoute("/api/status", get_system_status, methods=["GET"]),
        APIRoute("/frame.jpg", get_latest_frame, methods=["GET"]),
        APIRoute("/api/frame.jpg", get_latest_frame, methods=["GET"]),
    ]

    for route in reversed(custom_routes):
        server_app.router.routes.insert(0, route)

    logger.info("Custom API routes successfully injected into Gradio FastAPI router.")

    # Keep server running permanently
    demo.block_thread()
