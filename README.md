# Photovoltaic Monitoring System

This repository contains the photovoltaic monitoring system I developed for my Bachelor's Thesis in Computer Engineering at the University of Almería.

The project started with a simple question: **how can I tell whether a photovoltaic installation is producing what it should, and identify what may be causing a loss in performance?**

To explore that problem, I built a monitoring system around a real residential photovoltaic installation consisting of 12 solar panels with a total installed capacity of 5 kWp.

Rather than relying on a single measurement, the system combines several sources of information: real production data from the inverter, theoretical production based on weather and irradiance data, computer vision, and an experimental optical sensor designed to measure soiling.

The complete system runs automatically on a Linux server and was designed to operate continuously with minimal manual intervention.

The project received a final grade of **10/10 with Honors (Matrícula de Honor)**.

## How it works

One of the main parts of the project is the comparison between expected and actual photovoltaic production.

Real production data is obtained from Huawei FusionSolar, while meteorological and solar irradiance data from Open-Meteo is used to estimate how much energy the installation should be producing under the current conditions.

The system periodically compares both values and keeps track of deviations that may indicate a loss of performance.

But production data alone cannot explain why that loss is happening. For that reason, I experimented with two additional sources of information.

The first is computer vision. An ESP32-CAM captures images of a photovoltaic module and the images are processed to isolate the panel and analyze its surface. The processing pipeline was developed to identify objects, visible contamination and shaded areas that could affect production.

The second is a custom optical sensor built around an ESP32, a BPW34 photodiode and a laser. A reference glass is exposed to the same environment as the photovoltaic installation, and changes in the amount of transmitted light are used as an experimental indicator of accumulated soiling.

These different measurements are combined with automated logging and monitoring processes running on Linux.

## Automation

I wanted the project to work as an actual monitoring system rather than as a collection of scripts that had to be launched manually.

The software therefore runs on a dedicated Linux server using systemd services and timers. Different processes handle production monitoring, sensor measurements, image analysis and daily summaries.

A Telegram bot is also integrated into the system to provide monitoring information and notifications.

This allowed the prototype to run continuously on the real installation and collect information without requiring a computer to be operated manually.

## Technologies

The project brought together several areas that I wanted to explore within a single real-world system:

- Python
- OpenCV
- Selenium
- Requests
- Linux and systemd
- ESP32
- ESP32-CAM
- BPW34 photodiode
- Huawei FusionSolar
- Open-Meteo
- Roboflow
- Telegram Bot API

## Repository structure

```text
.
├── hardware/
│   ├── esp32-cam/
│   └── sensor-optico/
│
├── software/
│   └── vision/
│       └── src/
│
├── deployment/
│   └── systemd/
│
├── requirements.txt
└── README.md
```

`hardware/` contains the embedded code used by the ESP32 devices.

`software/vision/src/` contains the Python code responsible for data acquisition, production comparison, image processing, optical sensor logging and communication with Telegram.

`deployment/systemd/` contains the service and timer templates used to deploy the system on Linux.

## Running the software

Python dependencies are listed in `requirements.txt` and can be installed with:

```bash
pip install -r requirements.txt
```

The project requires configuration for external services before it can be executed.

Credentials are provided through environment variables rather than being stored in the source code. This includes FusionSolar credentials and Telegram configuration.

Generated data such as logs, captured images, session cookies, cache files and debugging outputs are excluded from the repository.

## About this repository

This repository represents the version of the system developed as part of my Bachelor's Thesis at the University of Almería.

Finishing the thesis was not the end of the project.

While building and testing the system, I found several limitations in the original approach and many areas that I wanted to explore further. What began as an academic project has therefore become the starting point for a much longer-term project around photovoltaic monitoring, data and automation.

I am continuing that work beyond the scope of the original thesis, with the goal of making the system more robust, scalable and useful in real photovoltaic installations.

The ongoing development is maintained separately from this public academic repository.
