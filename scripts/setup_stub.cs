using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

internal static class SetupStub
{
    private const string Marker = "\n--NOXLAB-SHARE-PAYLOAD-V1--\n";

    [STAThread]
    private static int Main()
    {
        string tempDir = Path.Combine(Path.GetTempPath(), "NoxLabShareSetup_" + Guid.NewGuid().ToString("N"));

        try
        {
            string selfPath = Assembly.GetExecutingAssembly().Location;
            byte[] selfBytes = File.ReadAllBytes(selfPath);
            byte[] markerBytes = Encoding.ASCII.GetBytes(Marker);
            int markerIndex = LastIndexOf(selfBytes, markerBytes);

            if (markerIndex < 0)
            {
                MessageBox.Show(
                    "The setup payload is missing or damaged.",
                    "NoxLab Share Setup",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return 1;
            }

            Directory.CreateDirectory(tempDir);
            string zipPath = Path.Combine(tempDir, "payload.zip");

            using (FileStream zip = File.Create(zipPath))
            {
                zip.Write(selfBytes, markerIndex + markerBytes.Length, selfBytes.Length - markerIndex - markerBytes.Length);
            }

            ZipFile.ExtractToDirectory(zipPath, tempDir);

            string installer = Path.Combine(tempDir, "Install.cmd");
            if (!File.Exists(installer))
            {
                MessageBox.Show(
                    "The setup installer command is missing.",
                    "NoxLab Share Setup",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return 1;
            }

            ProcessStartInfo startInfo = new ProcessStartInfo
            {
                FileName = installer,
                WorkingDirectory = tempDir,
                UseShellExecute = false,
                CreateNoWindow = true
            };

            using (Process process = Process.Start(startInfo))
            {
                process.WaitForExit();
                if (process.ExitCode != 0)
                {
                    MessageBox.Show(
                        "NoxLab Share setup did not finish successfully.",
                        "NoxLab Share Setup",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error);
                    return process.ExitCode;
                }
            }

            return 0;
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                ex.Message,
                "NoxLab Share Setup",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return 1;
        }
        finally
        {
            try
            {
                if (Directory.Exists(tempDir))
                {
                    Directory.Delete(tempDir, true);
                }
            }
            catch
            {
                // Temporary setup files can be cleaned by Windows later if any file is still in use.
            }
        }
    }

    private static int LastIndexOf(byte[] haystack, byte[] needle)
    {
        for (int i = haystack.Length - needle.Length; i >= 0; i--)
        {
            bool found = true;
            for (int j = 0; j < needle.Length; j++)
            {
                if (haystack[i + j] != needle[j])
                {
                    found = false;
                    break;
                }
            }

            if (found)
            {
                return i;
            }
        }

        return -1;
    }
}
