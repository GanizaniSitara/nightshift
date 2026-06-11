<#
.SYNOPSIS
    Dumb console primitive: read a console's visible tail, or inject text into it.
    PID-only; no personal paths; no classification (that lives in nudger.py).

    Used by Nightshift's nudger to read a managed agent session's screen and, when
    Python decides it's parked on a recoverable prompt, type the revival keystroke.
    Must run in the SAME interactive session as the target (AttachConsole cannot
    cross the session-0 boundary), so it's invoked by the in-session monitor, never
    by the LocalSystem watchdog service.

.PARAMETER Action
    'read' -> write {ok, tail} to OutFile.  'send' -> inject -Text, write {ok}.

.OUTPUTS
    JSON written to -OutFile (stdout is unusable after AttachConsole hijacks it).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][int]$CmdPid,
    [ValidateSet('read', 'send')][string]$Action = 'read',
    [int]$Lines = 30,
    [string]$Text = '',
    [Parameter(Mandatory = $true)][string]$OutFile
)

$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class ConIO {
    [StructLayout(LayoutKind.Sequential)] public struct COORD { public short X; public short Y; }
    [StructLayout(LayoutKind.Sequential)] public struct SMALL_RECT { public short Left; public short Top; public short Right; public short Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct CSBI {
        public COORD dwSize; public COORD dwCursorPosition; public short wAttributes;
        public SMALL_RECT srWindow; public COORD dwMaximumWindowSize;
    }
    [StructLayout(LayoutKind.Explicit, Size = 20)] public struct INPUT_RECORD {
        [FieldOffset(0)]  public ushort EventType;
        [FieldOffset(4)]  public int    bKeyDown;
        [FieldOffset(8)]  public ushort wRepeatCount;
        [FieldOffset(10)] public ushort wVirtualKeyCode;
        [FieldOffset(12)] public ushort wVirtualScanCode;
        [FieldOffset(14)] public char   UnicodeChar;
        [FieldOffset(16)] public uint   dwControlKeyState;
    }
    [DllImport("kernel32.dll")] public static extern bool FreeConsole();
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool AttachConsole(uint pid);
    [DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int h);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetConsoleScreenBufferInfo(IntPtr h, out CSBI info);
    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
    public static extern bool ReadConsoleOutputCharacter(IntPtr h, StringBuilder buf, uint len, COORD coord, out uint read);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool WriteConsoleInput(IntPtr h, INPUT_RECORD[] buf, uint len, out uint written);

    public static string Tail(uint pid, int lines) {
        FreeConsole();
        if (!AttachConsole(pid)) return null;
        IntPtr h = GetStdHandle(-11);
        CSBI info;
        if (!GetConsoleScreenBufferInfo(h, out info)) { FreeConsole(); return null; }
        int width = Math.Max(1, (int)info.dwSize.X);
        int startY = Math.Max(0, (int)info.dwCursorPosition.Y - lines);
        uint len = (uint)(width * lines);
        var sb = new StringBuilder((int)len);
        COORD c; c.X = 0; c.Y = (short)startY; uint read;
        bool ok = ReadConsoleOutputCharacter(h, sb, len, c, out read);
        FreeConsole();
        return ok ? sb.ToString().Replace("\0", "") : null;
    }

    public static bool Send(uint pid, string text) {
        FreeConsole();
        if (!AttachConsole(pid)) return false;
        IntPtr h = GetStdHandle(-10);
        string full = text + "\r";
        var r = new INPUT_RECORD[full.Length * 2];
        for (int i = 0; i < full.Length; i++) {
            char ch = full[i];
            ushort vk = (ch == '\r') ? (ushort)0x0D : (ushort)0;
            r[i*2].EventType = 1; r[i*2].bKeyDown = 1; r[i*2].wRepeatCount = 1; r[i*2].wVirtualKeyCode = vk; r[i*2].UnicodeChar = ch;
            r[i*2+1].EventType = 1; r[i*2+1].bKeyDown = 0; r[i*2+1].wRepeatCount = 1; r[i*2+1].wVirtualKeyCode = vk; r[i*2+1].UnicodeChar = ch;
        }
        uint w; bool ok = WriteConsoleInput(h, r, (uint)r.Length, out w);
        FreeConsole();
        return ok;
    }
}
'@

if ($Action -eq 'read') {
    $tail = [ConIO]::Tail([uint32]$CmdPid, $Lines)
    $obj = [pscustomobject]@{ ok = ($null -ne $tail); tail = [string]$tail }
} else {
    $sent = [ConIO]::Send([uint32]$CmdPid, $Text)
    $obj = [pscustomobject]@{ ok = [bool]$sent; tail = '' }
}

[ConIO]::FreeConsole() | Out-Null
[System.IO.File]::WriteAllText($OutFile, ($obj | ConvertTo-Json -Compress), [System.Text.Encoding]::UTF8)
