import { useEffect, useState } from "react";
import LandingPage from "./components/LandingPage";
import AuditDashboard from "./components/AuditDashboard";
import MatrixRain from "./components/MatrixRain";

const AUDIT_PATH_REGEX = /^\/audit\/([A-Za-z0-9_-]+)\/?$/;

interface Route {
  name: "landing" | "audit";
  sessionId?: string;
}

function parseRoute(pathname: string): Route {
  if (pathname === "/" || pathname === "") return { name: "landing" };
  const match = pathname.match(AUDIT_PATH_REGEX);
  if (match) return { name: "audit", sessionId: match[1] };
  return { name: "landing" };
}

function TerminalBar({ pathname }: { pathname: string }) {
  return (
    <div className="border-b border-kali-border bg-kali-bg px-4 sm:px-6">
      <div className="mx-auto flex max-w-[1280px] items-center py-2 font-mono text-[12px] text-kali-dim">
        <span>nodoxx@local ~ %&nbsp;</span>
        <span className="text-kali-text">{pathname || "/"}</span>
        <span
          aria-hidden="true"
          className="ml-1 inline-block h-[0.85em] w-[0.5ch] bg-kali-text animate-caret-blink"
        />
      </div>
    </div>
  );
}

export default function App() {
  const [route, setRoute] = useState<Route>(() =>
    parseRoute(window.location.pathname),
  );
  const [pathname, setPathname] = useState<string>(window.location.pathname);

  useEffect(() => {
    const current = parseRoute(window.location.pathname);
    if (
      current.name === "landing" &&
      window.location.pathname !== "/" &&
      window.location.pathname !== ""
    ) {
      window.history.replaceState({}, "", "/");
      setPathname("/");
    } else {
      setPathname(window.location.pathname);
    }
    setRoute(current);

    const onPop = () => {
      setRoute(parseRoute(window.location.pathname));
      setPathname(window.location.pathname);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  return (
    <>
      <MatrixRain />
      <TerminalBar pathname={pathname} />
      {route.name === "audit" && route.sessionId ? (
        <AuditDashboard sessionId={route.sessionId} />
      ) : (
        <LandingPage />
      )}
    </>
  );
}
