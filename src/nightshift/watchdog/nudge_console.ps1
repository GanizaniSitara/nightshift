<#
.SYNOPSIS
    Probe a console's last lines, classify a recoverable "stuck" prompt, and
    optionally inject the keystroke that revives it. PID-only; no personal paths.

    Used by Nightshift's nudger to unstick a managed agent session that has
    parked on a rate-limit or context-compaction prompt overnight. Must run in
    the SAME interactive session as the target console (AttachConsole cannot
    cross the session-0 boundary), so this is invoked by the in-session monitor,
    never by the LocalSystem watchdog service.

.PARAMETER CmdPid
    PID of the cmd.exe (or console host) whose buffer to read/inject into.

.PARAMETER Apply
    When set, inject the revival keystrokes. Otherwise probe + classify only.

.OUTPUTS
    One JSON line: {"state":"...","action":"...","verified":<bool>}
    state: rate-limit | context-limit | working-or-unknown | attach-failed
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][int]$CmdPid,
    [int]$Lines = 30,
    [switch]$Apply,
    # Result JSON is written here. Required for programmatic use: after AttachConsole
    # the process's stdout is hijacked to the target console, so it cannot be piped back.
    [string]$OutFile = ''
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

function Classify([string]$text) {
    if (-not $text) { return 'attach-failed' }
    $rate = ($text -match "You've hit your limit" -or $text -match 'usage limit') -and `
            ($text -match 'What do you want to do\?' -or $text -match '/rate-limit-options' -or `
             $text -match 'upgrade to increase your usage limit' -or $text -match 'resets\s+\d{1,2}:\d{2}')
    if ($rate) { return 'rate-limit' }
    $ctx = ($text -match 'context window' -or $text -match 'compaction' -or `
            $text -match 'compact the conversation' -or ($text -match 'token' -and $text -match 'limit'))
    if ($ctx) { return 'context-limit' }
    return 'working-or-unknown'
}

$tail = [ConIO]::Tail([uint32]$CmdPid, $Lines)
$state = Classify $tail
$action = 'none'
$verified = $false

if ($Apply -and ($state -eq 'rate-limit' -or $state -eq 'context-limit')) {
    if ($state -eq 'context-limit') {
        $sent = [ConIO]::Send([uint32]$CmdPid, '/compact')
        $action = if ($sent) { 'compact-sent' } else { 'inject-failed' }
    } else {
        # Rate limit: dismiss the prompt (bare Enter) then type "continue".
        [void][ConIO]::Send([uint32]$CmdPid, '')
        Start-Sleep -Milliseconds 350
        $sent = [ConIO]::Send([uint32]$CmdPid, 'continue')
        $action = if ($sent) { 'continue-sent' } else { 'inject-failed' }
    }
    if ($action -ne 'inject-failed') {
        Start-Sleep -Milliseconds 700
        $after = [ConIO]::Tail([uint32]$CmdPid, $Lines)
        $verified = ((Classify $after) -eq 'working-or-unknown')
    }
}

[ConIO]::FreeConsole() | Out-Null  # release the attached console before any further I/O
$result = [pscustomobject]@{ state = $state; action = $action; verified = $verified } | ConvertTo-Json -Compress
if ($OutFile) {
    [System.IO.File]::WriteAllText($OutFile, $result, [System.Text.Encoding]::UTF8)
} else {
    Write-Output $result
}
