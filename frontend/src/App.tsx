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

export default function App() {
  const [route, setRoute] = useState<Route>(() =>
    parseRoute(window.location.pathname),
  );

  useEffect(() => {
    const current = parseRoute(window.location.pathname);
    if (
      current.name === "landing" &&
      window.location.pathname !== "/" &&
      window.location.pathname !== ""
    ) {
      window.history.replaceState({}, "", "/");
    }
    setRoute(current);

    const onPop = () => setRoute(parseRoute(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  return (
    <>
      <MatrixRain />
      <div className="relative z-10">
        {route.name === "audit" && route.sessionId ? (
          <AuditDashboard sessionId={route.sessionId} />
        ) : (
          <LandingPage />
        )}
      </div>
    </>
  );
}
