import "./App.css";
import { AppProvider } from "./context";
import Sidebar from "./components/Sidebar";
import TopBar from "./components/TopBar";
import ChatArea from "./components/ChatArea";
import RightPanel from "./components/RightPanel";
import SetupWizard from "./components/SetupWizard";
import { useEffect, useState } from "react";
import { fetchSetupStatus } from "./api";

function AppLayout() {
  return (
    <div className="app-shell">
      <Sidebar />
      <div className="main">
        <TopBar />
        <div className="content-split">
          <ChatArea />
          <RightPanel />
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [setupGate, setSetupGate] = useState<"checking" | "setup" | "app">("checking");

  useEffect(() => {
    let cancelled = false;

    async function checkSetup() {
      if (new URLSearchParams(window.location.search).get("setup") === "1") {
        setSetupGate("setup");
        return;
      }
      const status = await fetchSetupStatus();
      if (cancelled) return;
      setSetupGate(status.setup_complete ? "app" : "setup");
    }

    void checkSetup();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <AppProvider>
      {setupGate === "checking" ? (
        <div className="setup-screen">
          <div className="setup-shell">Checking Bio-Harness setup...</div>
        </div>
      ) : setupGate === "setup" ? (
        <SetupWizard onContinue={() => setSetupGate("app")} />
      ) : (
        <AppLayout />
      )}
    </AppProvider>
  );
}
