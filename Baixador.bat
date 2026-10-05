@echo off
rem ============================================================
rem  Baixador de Footage - abrir sem terminal (duplo clique)
rem
rem  As mensagens DESTE arquivo sao propositalmente sem acento.
rem  O cmd.exe le o .bat na codepage OEM do sistema, e acento
rem  escrito aqui vira lixo na tela. O texto que sai do Python
rem  continua em UTF-8 normalmente (chcp 65001 abaixo).
rem ============================================================
setlocal

rem Sempre a pasta do proprio .bat, venha o duplo clique de onde vier.
cd /d "%~dp0"

rem A porta e a de src/web/app.py. tests/test_atalho.py cobra que sejam
rem iguais, para o atalho nao apontar para o lugar errado em silencio.
set PORTA=8000
set ENDERECO=http://127.0.0.1:%PORTA%

rem Ja esta no ar? A pergunta e feita a API, nao a porta: assim um outro
rem programa ocupando a 8000 nao passa por Baixador. Se o curl nao existir
rem (Windows antigo), o errorlevel nao sera 0 e seguimos para subir.
curl -s -f -m 2 -o NUL "%ENDERECO%/api/config"
if errorlevel 1 goto :subir

rem Ja esta rodando: so abre a janela, no modo app do Edge (sem barra de
rem endereco, como um programa), igual ao que o src/web/app.py faz. Labels e
rem goto, e nao um bloco ( ): o "(x86)" do caminho fecharia o parentese.
echo O Baixador ja esta rodando. Abrindo a janela.
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" goto :aba_comum
start "" "%EDGE%" --app=%ENDERECO% --window-size=1440,900
exit /b 0
:aba_comum
start "" "%ENDERECO%"
exit /b 0

:subir

rem A janela se reabre minimizada uma unica vez, para nao ficar na frente
rem do navegador. Ela continua na barra de tarefas: e por ela que se para
rem o servidor, e e nela que a mensagem de erro aparece.
if not "%~1"=="--rodando" (
    start "Baixador de Footage" /min "%~f0" --rodando
    exit /b 0
)

if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=python
)

chcp 65001 >NUL
title Baixador de Footage - %ENDERECO%
"%PYTHON%" -m src.web
set CODIGO=%ERRORLEVEL%

if not "%CODIGO%"=="0" (
    title ERRO - Baixador de Footage
    echo.
    echo ============================================================
    echo  O Baixador encerrou com erro ^(codigo %CODIGO%^).
    echo  A mensagem esta logo acima, nesta mesma janela.
    echo.
    echo  Se disser que o modulo nao foi encontrado, falta instalar
    echo  as dependencias: pip install -r requirements.txt
    echo ============================================================
    echo.
    pause
)
exit /b %CODIGO%
