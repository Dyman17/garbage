# HerbiBioS Cloud Firmware (ESP32-CAM)

Прошивка для платы AI-Thinker ESP32-CAM с беспроводной передачей кадров на облачный сервер и управлением 3 сервоприводами.

## Подключение сервоприводов к ESP32:
* **BIN_1 (Пластик):** GPIO 12
* **BIN_2 (Металл / Банки):** GPIO 13
* **BIN_3 (Бумага / Картон):** GPIO 14
* **Питание сервоприводов:** 5V (желательно от отдельного источника питания 5V 2A с общей землей GND).

## Настройка перед прошивкой:
Откройте файл `HerbiBioS_Cloud/HerbiBioS_Cloud.ino`:
1. Укажите имя и пароль вашей домашней сети:
   ```cpp
   const char* WIFI_SSID = "Ваш_WiFi";
   const char* WIFI_PASSWORD = "Ваш_Пароль";
   ```
2. Укажите адрес вашего сервиса на Hugging Face Spaces:
   ```cpp
   const char* SERVER_URL = "https://your-username-herbibios-ai.hf.space/api/frame";
   ```

## Настройки в Arduino IDE:
1. Плата: **AI Thinker ESP32-CAM**
2. CPU Frequency: **240MHz (WiFi/BT)**
3. Flash Frequency: **80MHz**
4. Flash Mode: **QIO**
5. Partition Scheme: **Huge APP (3MB No OTA/1MB SPIFFS)**
6. PSRAM: **Enabled**
7. Библиотеки: **ESP32Servo** (устанавливается через Скетч -> Подключить библиотеку -> Управление библиотеками).

