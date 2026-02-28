# Kick Drops Miner

Aplicación de escritorio para gestionar campañas de Drops en Kick y minarlas automáticamente desde una cola de canales.

## Funciones principales

- Inicio de sesión asistido y persistencia de sesión entre reinicios.
- Consulta de campañas y progreso real desde la API web de Kick.
- Cola automática de canales con selección inteligente:
  - solo canales en directo,
  - prioridad al canal con más viewers dentro de la campaña.
- Worker en modo oculto (headless/offscreen) para no abrir ventanas durante el minado.
- Auto-claim de recompensas habilitado siempre.
- Inventario visual de campañas y drops.

## Requisitos

- Windows 10/11
- Python 3.10+
- Google Chrome o Microsoft Edge instalado

## Instalación

```powershell
py -3 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Flujo recomendado

1. Abrir la app.
2. Pulsar `Iniciar sesion` y completar login.
3. Pulsar `Actualizar`.
4. Añadir canales/campañas a la cola desde `General`.
5. Iniciar cola.

## Archivos generados

- `kick_config.json`: configuración local y cola.
- `cookies/kick.com.json`: cookies de sesión exportadas.
- `chrome_data/`: perfil de navegador de la app.
- `cache/reward_thumbs/`: miniaturas cacheadas.
- `logs/app.log`: logs de la sesión actual.

## Diagnóstico rápido

```powershell
py -3 diagnose_env.py
```

Genera un JSON con estado del entorno, cookies y conectividad básica de endpoints de Kick.
