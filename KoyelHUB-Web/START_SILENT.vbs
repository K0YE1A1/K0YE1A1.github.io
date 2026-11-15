Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\metaq\OneDrive\Desktop\ANTIGRAVITY"
WshShell.Run "pythonw.exe web_panel.py", 0, False
