@echo off
:: ============================================================
:: Melhoria 6 — Instalar Polymarket Bot como serviço Windows
:: usando NSSM (Non-Sucking Service Manager)
::
:: Pré-requisitos:
::   1. Baixe o NSSM em https://nssm.cc/download
::   2. Coloque nssm.exe em C:\nssm\ ou adicione ao PATH
::   3. Execute este script como ADMINISTRADOR
:: ============================================================

setlocal

:: ── Configurações — ajuste conforme necessário ──────────────────────────
set SERVICE_NAME=PolymarketBot
set PYTHON_EXE=python
set BOT_DIR=%~dp0..
set BOT_SCRIPT=%BOT_DIR%\main.py
set LOG_DIR=%BOT_DIR%\logs
set NSSM=nssm

:: Verifica se está rodando como admin
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERRO: Execute este script como Administrador.
    pause
    exit /b 1
)

:: Verifica NSSM
where %NSSM% >nul 2>&1
if %errorLevel% neq 0 (
    echo ERRO: NSSM nao encontrado. Baixe em https://nssm.cc/download
    echo e coloque nssm.exe no PATH.
    pause
    exit /b 1
)

:: Cria diretório de logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo Instalando servico: %SERVICE_NAME%

:: Remove servico anterior (se existir)
%NSSM% stop %SERVICE_NAME% >nul 2>&1
%NSSM% remove %SERVICE_NAME% confirm >nul 2>&1

:: Instala o servico
%NSSM% install %SERVICE_NAME% %PYTHON_EXE%
%NSSM% set %SERVICE_NAME% AppParameters "%BOT_SCRIPT%"
%NSSM% set %SERVICE_NAME% AppDirectory "%BOT_DIR%"

:: Reiniciar automaticamente após crash
%NSSM% set %SERVICE_NAME% AppRestartDelay 10000
%NSSM% set %SERVICE_NAME% AppExit Default Restart

:: Logs de stdout/stderr do serviço
%NSSM% set %SERVICE_NAME% AppStdout "%LOG_DIR%\service_stdout.log"
%NSSM% set %SERVICE_NAME% AppStderr "%LOG_DIR%\service_stderr.log"
%NSSM% set %SERVICE_NAME% AppRotateFiles 1
%NSSM% set %SERVICE_NAME% AppRotateBytes 10485760

:: Iniciar com o Windows
%NSSM% set %SERVICE_NAME% Start SERVICE_AUTO_START

:: Descrição do serviço
%NSSM% set %SERVICE_NAME% Description "Polymarket Trading Bot - paper/live trading automatico"

echo.
echo Servico instalado com sucesso!
echo.
echo Comandos uteis:
echo   Iniciar:   nssm start %SERVICE_NAME%
echo   Parar:     nssm stop %SERVICE_NAME%
echo   Status:    nssm status %SERVICE_NAME%
echo   Remover:   nssm remove %SERVICE_NAME% confirm
echo   Logs:      type %LOG_DIR%\service_stderr.log
echo.
echo Iniciando o servico...
%NSSM% start %SERVICE_NAME%

pause
