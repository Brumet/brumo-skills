' Arranca Ollama (la IA) y brumo.cmd sin abrir ninguna ventana (lo usa el acceso directo de la carpeta Inicio).
' Ollama se enciende solo su servidor, sin la ventana de la aplicación. Si ya estaba encendido, no pasa nada.
Set sh = CreateObject("WScript.Shell")
carpeta = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
ollama = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
If CreateObject("Scripting.FileSystemObject").FileExists(ollama) Then
    sh.Environment("Process")("OLLAMA_KEEP_ALIVE") = "24h"
    sh.Run """" & ollama & """ serve", 0, False
End If
sh.Run """" & carpeta & "\brumo.cmd""", 0, False
