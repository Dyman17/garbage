#include <Arduino.h>
#include "esp_camera.h"
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ESP32Servo.h>

// ================= НАСТРОЙКИ ПОДКЛЮЧЕНИЯ =================
// 1. Укажите имя и пароль вашей Wi-Fi сети (с выходом в интернет)
const char* WIFI_SSID = "DD";
const char* WIFI_PASSWORD = "dddddddd";

// 2. Адрес вашего сервера в облаке Hugging Face Spaces
const char* SERVER_URL = "https://dyman17-garbage.hf.space/api/frame";

// 3. Интервал между отправками кадров (в миллисекундах)
const unsigned long CAPTURE_INTERVAL_MS = 2500; // каждые 2.5 секунды
// =========================================================

// ================= НАЗНАЧЕНИЕ ПИНОВ ======================
// Пины сервоприводов (BIN_1 -> GPIO12, BIN_2 -> GPIO13, BIN_3 -> GPIO14)
constexpr int SERVO_PINS[] = {12, 13, 14};
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

void moveServoTo(int index, int angle) {
  if (index < 0 || index >= 3) return;
  attachServo(index);
  servos[index].write(angle);
}

void executeTimedOpen(int index, int openAngle, int closedAngle, unsigned long holdMs) {
  if (index < 0 || index >= 3) return;
  Serial.printf("[SERVO] Открытие BIN_%d: угол %d на %lu мс (закрытие: %d)\n", index + 1, openAngle, holdMs, closedAngle);
  moveServoTo(index, openAngle);
  activeServoIndex = index;
  activeServoClosedAngle = closedAngle;
  servoCloseTimestamp = millis() + holdMs;
}

void checkServoAutoClose() {
  if (activeServoIndex >= 0 && millis() >= servoCloseTimestamp) {
    Serial.printf("[SERVO] Закрытие BIN_%d: угол %d\n", activeServoIndex + 1, activeServoClosedAngle);
    moveServoTo(activeServoIndex, activeServoClosedAngle);
    delay(300); // даем сервоприводу завершить поворот
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
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  if (psramFound()) {
    config.frame_size = FRAMESIZE_VGA; // 640x480 — оптимально для YOLOv8
    config.jpeg_quality = 12;          // 10-12 хорошее качество при небольшом весе
    config.fb_count = 2;
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

void connectWiFi() {
  Serial.printf("\n[WiFi] Подключение к %s ", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[WiFi] Подключено!");
    Serial.print("[WiFi] IP адрес: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\n[WiFi] Не удалось подключиться к Wi-Fi. Проверьте SSID и пароль.");
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
    int openAngle = 90;
    int closedAngle = 0;
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
    connectWiFi();
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
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n====================================");
  Serial.println("  HerbiBioS Cloud Client v2.0 (IoT)  ");
  Serial.println("====================================");

  setupServos();
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
