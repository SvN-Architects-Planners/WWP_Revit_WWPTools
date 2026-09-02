import os
import sys

from pyrevit import forms, script
from pyrevit.coreutils import git as pygit
from System.Windows import Visibility


script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

from WWP_versioning import get_installed_version
import WWP_telemetry
import WWP_uiUtils as ui


WEBSITE_URL = "https://wwparchitects.com"
GUIDEBOOK_URL = "https://svn-architects-planners-inc.gitbook.io/svn-guidebooks/w7kFyDX0kRTb27slSn93/wwp-technical-guidebook/section-2-or-revit/2.5-or-plugins/2.5.1-or-wwp-tools-toolbar/6-links#wwp-website"
GITHUB_URL = "https://github.com/WWP-Architects-Planners/WWP_Revit_WWPTools"
RELEASE_URL = "https://github.com/WWP-Architects-Planners/WWP_Revit_WWPTools/releases/latest"


def _extension_root():
    return os.path.normpath(os.path.join(script_dir, "..", "..", ".."))


def _discover_repo():
    try:
        repo_path = pygit.libgit.Repository.Discover(_extension_root())
    except Exception:
        return None
    if not repo_path:
        return None
    try:
        return pygit.get_repo(repo_path)
    except Exception:
        return None


class AboutWindow(forms.WPFWindow):
    def __init__(self, xaml_file):
        forms.WPFWindow.__init__(self, xaml_file)

        version = get_installed_version("dev")
        self.logo_path = os.path.normpath(os.path.join(_extension_root(), "lib", "WWPtools-logo.png"))
        self.Title = "About WWPTools v{}".format(version)
        self.TitleText.Text = "WWPTools"
        self.SubtitleText.Text = "WW+P Revit productivity tools and automation workflows."
        self.VersionText.Text = "Version v{}".format(version)
        self.BodyText.Text = (
            "Use this panel to quickly open WWP resources, release notes, and the GitHub repository."
        )
        self.FooterText.Text = "Click anywhere outside the content panel or press any key to close."

        ui.uiUtils_load_logo(self.LogoImage, self.logo_path)

        repo_info = _discover_repo()
        if repo_info:
            self.BranchBadge.Visibility = Visibility.Visible
            self.CommitBadge.Visibility = Visibility.Visible
            self.BranchText.Text = "Branch {}".format(repo_info.branch)
            self.CommitText.Text = "Commit {}".format(repo_info.last_commit_hash[:7])

        self.WebsiteButton.Click += self._open_website
        self.GuidebookButton.Click += self._open_guidebook
        self.GithubButton.Click += self._open_github
        self.ReleaseButton.Click += self._open_release

        self.TelemetryCheckBox.IsChecked = WWP_telemetry.is_telemetry_enabled()

    def _open_website(self, sender, args):
        script.open_url(WEBSITE_URL)

    def _open_guidebook(self, sender, args):
        script.open_url(GUIDEBOOK_URL)

    def _open_github(self, sender, args):
        script.open_url(GITHUB_URL)

    def _open_release(self, sender, args):
        script.open_url(RELEASE_URL)

    def on_telemetry_changed(self, sender, args):
        WWP_telemetry.set_telemetry_enabled(self.TelemetryCheckBox.IsChecked == True)

    def handleclick(self, sender, args):
        self.Close()


AboutWindow("AboutWindow.xaml").show_dialog()
