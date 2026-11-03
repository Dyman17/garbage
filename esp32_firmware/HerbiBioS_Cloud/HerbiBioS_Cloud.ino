#include <Arduino.h>
#include "esp_camera.h"
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ESP32Servo.h>
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// ================= НАСТРОЙКИ ПОДКЛЮЧЕНИЯ =================
// 1. Укажите имя и пароль вашей Wi-Fi сети (с выходом в интернет)
const char* WIFI_SSID = "DD";
const char* WIFI_PASSWORD = "dddddddd";

// 2. Адрес вашего сервера в облаке Hugging Face Spaces (в Gradio 6 бэкенд на /gradio_api/frame)
const char* SERVER_URL = "https://dyman17-garbage.hf.space/gradio_api/frame";

// 3. Интервал между отправками кадров (в миллисекундах)
const unsigned long CAPTURE_INTERVAL_MS = 2500; // каждые 2.5 секунды
// =========================================================

// ================= НАЗНАЧЕНИЕ ПИНОВ ======================
// Пины сервоприводов (BIN_1 -> GPIO12, BIN_2 -> GPIO13, BIN_3 -> GPIO15)
constexpr int SERVO_PINS[] = {12, 13, 15};
constexpr int SERVO_MIN_PULSE_US = 500;
constexpr int SERVO_MAX_PULSE_US = 2400;

// Пины камеры (AI-Thinker ESP32-CAM)
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22
// =========================================================

Servo servos[3];
bool servoAttached[3] = {false, false, false};
unsigned long lastCaptureTime = 0;

// Конфигурация углов сервоприводов (по умолчанию: закрыто = -90°, открыто = 45°)
int binClosedAngle[3] = {-90, -90, -90};
int binOpenAngle[3]   = {45, 45, 45};

// Переменные для неблокирующего закрытия крышки
int activeServoIndex = -1;
int activeServoClosedAngle = 0;
unsigned long servoCloseTimestamp = 0;

void setupServos() {
  for (int i = 0; i < 3; ++i) {
    servos[i].setPeriodHertz(50);
  }
}

void attachServo(int index) {
  if (index < 0 || index >= 3) return;
  if (!servoAttached[index] || !servos[index].attached()) {
    servos[index].attach(SERVO_PINS[index], SERVO_MIN_PULSE_US, SERVO_MAX_PULSE_US);
    servoAttached[index] = true;
  }
}

void detachServo(int index) {
  if (index < 0 || index >= 3) return;
  if (servos[index].attached()) {
    servos[index].detach();
    servoAttached[index] = false;
  }
}

// Преобразование логического угла в физический ШИМ угол сервопривода (0°..180°).
// Если в системе используются отрицательные углы (например -90° закрыто, 0° открыто),
// то -90° транслируется в физический 0°, а 0° — в физический 90° (+90° сдвиг).
int toPhysicalAngle(int index, int angle) {
  int closedA = (index >= 0 && index < 3) ? binClosedAngle[index] : -90;
  int openA   = (index >= 0 && index < 3) ? binOpenAngle[index] : 0;

  if (closedA < 0 || openA < 0 || angle < 0) {
    return constrain(angle + 90, 0, 180);
  }
  return constrain(angle, 0, 180);
}

void moveServoTo(int index, int angle) {
  if (index < 0 || index >= 3) return;
  int physicalPWM = toPhysicalAngle(index, angle);
  Serial.printf("[SERVO] BIN_%d: угол %d° -> ШИМ %d°\n", index + 1, angle, physicalPWM);
  attachServo(index);
  servos[index].write(physicalPWM);
}

void executeTimedOpen(int index, int openAngle, int closedAngle, unsigned long holdMs) {
  if (index < 0 || index >= 3) return;
  binClosedAngle[index] = closedAngle;
  binOpenAngle[index] = openAngle;

  int pOpen = toPhysicalAngle(index, openAngle);
  int pClosed = toPhysicalAngle(index, closedAngle);

  Serial.printf("[SERVO] Открытие BIN_%d: логич %d° (ШИМ %d°) на %lu мс, закрытие логич %d° (ШИМ %d°)\n",
                index + 1, openAngle, pOpen, holdMs, closedAngle, pClosed);
  attachServo(index);
  servos[index].write(pOpen);

  activeServoIndex = index;
  activeServoClosedAngle = pClosed;
  servoCloseTimestamp = millis() + holdMs;
}

void checkServoAutoClose() {
  if (activeServoIndex >= 0 && millis() >= servoCloseTimestamp) {
    Serial.printf("[SERVO] Закрытие BIN_%d: возврат в ШИМ %d°\n", activeServoIndex + 1, activeServoClosedAngle);
    attachServo(activeServoIndex);
    servos[activeServoIndex].write(activeServoClosedAngle);
    delay(800); // 800 мс — даем сервоприводу плотно дожать крышку до упора
    detachServo(activeServoIndex); // отключаем ШИМ, чтобы сервопривод не гудел
    activeServoIndex = -1;
  }
}

bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 16000000; // 16MHz устраняет переполнение DMA буфера (FB-OVF)
  config.pixel_format = PIXFORMAT_JPEG;

#if defined(CAMERA_GRAB_LATEST)
  config.grab_mode = CAMERA_GRAB_LATEST; // всегда брать только свежий кадр без забивания очереди
#endif

  if (psramFound()) {
    config.frame_size = FRAMESIZE_VGA; // 640x480 — оптимально для YOLOv8
    config.jpeg_quality = 12;          // 10-12 хорошее качество при небольшом весе
    config.fb_count = 1;               // 1 буфер исключает накопление старых кадров и FB-OVF
  } else {
    config.frame_size = FRAMESIZE_QVGA; // 320x240
    config.jpeg_quality = 15;
    config.fb_count = 1;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[CAMERA] Ошибка инициализации камеры: 0x%x\n", err);
    return false;
  }
  Serial.println("[CAMERA] Камера успешно инициализирована.");
  return true;
}

unsigned long lastWiFiReconnectAttempt = 0;

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  Serial.printf("\n[WiFi] Подключение к %s ", WIFI_SSID);
  
  // Сбрасываем предыдущую зависшую попытку, чтобы не было ошибки "wifi:sta is connecting, cannot set config"
  WiFi.disconnect(true);
  delay(300);

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(false); // Отключаем засыпание Wi-Fi модема для стабильной связи
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 25) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[WiFi] Подключено успешно!");
    Serial.print("[WiFi] IP адрес: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\n[WiFi] Не удалось подключиться к Wi-Fi. Проверьте правильность SSID/пароля.");
  }
}

// Парсер серверных команд (поддерживает и простой поиск, чтобы не требовать тяжелых библиотек)
void parseAndExecuteCommand(const String& payload) {
  if (payload.indexOf("\"command\":null") != -1) {
    return; // Нет команд для исполнения
  }

  int binIdx = -1;
  if (payload.indexOf("BIN_1") != -1) binIdx = 0;
  else if (payload.indexOf("BIN_2") != -1) binIdx = 1;
  else if (payload.indexOf("BIN_3") != -1) binIdx = 2;

  if (binIdx == -1) return;

  // Если это ручная установка кастомного угла
  if (payload.indexOf("\"type\":\"custom\"") != -1) {
    int anglePos = payload.indexOf("\"angle\":");
    if (anglePos != -1) {
      int angle = payload.substring(anglePos + 8).toInt();
      Serial.printf("[COMMAND] Кастомный угол для BIN_%d -> %d°\n", binIdx + 1, angle);
      moveServoTo(binIdx, angle);
    }
    return;
  }

  // Если это открытие крышки на время (автоматическое или ручное)
  if (payload.indexOf("timed_open") != -1) {
    int openAngle = 45;
    int closedAngle = -90;
    unsigned long holdMs = 3000;

    int openPos = payload.indexOf("\"open_angle\":");
    if (openPos != -1) openAngle = payload.substring(openPos + 13).toInt();

    int closedPos = payload.indexOf("\"closed_angle\":");
    if (closedPos != -1) closedAngle = payload.substring(closedPos + 15).toInt();

    int holdPos = payload.indexOf("\"hold_ms\":");
    if (holdPos != -1) holdMs = payload.substring(holdPos + 10).toInt();

    executeTimedOpen(binIdx, openAngle, closedAngle, holdMs);
  }
}

void captureAndSendFrame() {
  if (WiFi.status() != WL_CONNECTED) {
    if (millis() - lastWiFiReconnectAttempt > 10000) {
      lastWiFiReconnectAttempt = millis();
      connectWiFi();
    }
    return;
  }

  // Не прерываем снимок, если прямо сейчас открыта крышка
  if (activeServoIndex >= 0) {
    return;
  }

  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("[CAMERA] Ошибка захвата кадра");
    return;
  }

  WiFiClientSecure client;
  client.setInsecure(); // Отключаем валидацию SSL-сертификатов для экономии ОЗУ ESP32
  client.setTimeout(6); // Таймаут 6 сек

  HTTPClient https;
  if (https.begin(client, SERVER_URL)) {
    https.addHeader("Content-Type", "image/jpeg");
    https.setTimeout(6000);

    unsigned long startSend = millis();
    int httpResponseCode = https.POST(fb->buf, fb->len);
    unsigned long totalTime = millis() - startSend;

    if (httpResponseCode == 200) {
      String response = https.getString();
      Serial.printf("[HTTP 200] Кадр (%u байт) отправлен за %lu мс\n", fb->len, totalTime);
      parseAndExecuteCommand(response);
    } else {
      Serial.printf("[HTTP ERROR] Код ответа сервера: %d (%lu мс)\n", httpResponseCode, totalTime);
    }
    https.end();
  } else {
    Serial.println("[HTTP] Не удалось начать HTTPS соединение.");
  }

  esp_camera_fb_return(fb);
}

void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0); // Отключение детектора просадки (Brownout)
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n====================================");
  Serial.println("  HerbiBioS Cloud Client v2.0 (IoT)  ");
  Serial.println("====================================");

  setupServos();
  // Устанавливаем сервоприводы в исходное закрытое положение при старте
  for (int i = 0; i < 3; i++) {
    int initClosedPWM = toPhysicalAngle(i, binClosedAngle[i]);
    attachServo(i);
    servos[i].write(initClosedPWM);
    delay(300);
    detachServo(i);
  }

  if (!initCamera()) {
    Serial.println("[FATAL] Не удалось запустить камеру!");
  }

  connectWiFi();
}

void loop() {
  // 1. Проверяем не пора ли закрыть открытую крышку
  checkServoAutoClose();

  // 2. Отправляем кадр на сервер через заданный интервал
  if (millis() - lastCaptureTime >= CAPTURE_INTERVAL_MS) {
    lastCaptureTime = millis();
    captureAndSendFrame();
  }

  delay(20);
}
