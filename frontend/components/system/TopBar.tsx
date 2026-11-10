"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Bell, LogIn, LogOut, Menu, Moon, Plus, Search, Sun, X } from "lucide-react";
import { useTheme } from "next-themes";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useApprovals, useUI } from "@/lib/store/app";
import { useSession } from "@/lib/store/auth";
import { isLiveContext, useLiveApprovals } from "@/lib/store/live";
import { runtimeMode } from "@/lib/runtime-config";
import { BusinessSwitcher } from "./BusinessSwitcher";
import { SidebarLinks } from "./Sidebar";
import { Avatar, Badge } from "./badges";
import { buttonClass, IconButton } from "./controls";
import { PrimaryLink } from "./ui";
import { transition } from "@/lib/motion";
import { useModalSurface } from "@/lib/use-modal-surface";

export function TopBar() {
  const { setCommandOpen } = useUI();
  const { theme, setTheme } = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);
  const drawerRef = useRef<HTMLDivElement | null>(null);
  const { status, user, logout, currentBusinessId } = useSession();
  const demoPending = useApprovals(
    (s) => s.approvals.filter((approval) => approval.status === "pending").length,
  );
  const livePending = useLiveApprovals(
    (s) => s.approvals.filter((approval) => approval.status === "pending").length,
  );
  // LIVE never borrows seed badge counts while the session is unresolved.
  const pending =
    runtimeMode === "live" || isLiveContext(status, currentBusinessId) ? livePending : demoPending;
  const displayName =
    status === "authenticated" && user
      ? user.displayName
      : runtimeMode === "live"
        ? "Guest"
        : "Demo operator";
  const router = useRouter();

  useModalSurface(drawerRef, menuOpen);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  return (
    <>
      <header className="flex h-(--flux-topbar-h) shrink-0 items-center gap-2 border-b border-line bg-surface/80 px-3 backdrop-blur sm:gap-3 sm:px-4 lg:px-6">
        <IconButton
          variant="outline"
          label="Open navigation"
          onClick={() => setMenuOpen(true)}
          className="shrink-0 lg:hidden"
        >
          <Menu className="h-4 w-4" />
        </IconButton>
        <button
          onClick={() => setCommandOpen(true)}
          className="flex h-9 min-w-0 max-w-md flex-1 items-center gap-2 rounded-control border border-line bg-surface-sunken px-3 text-body-sm text-fg-muted transition-colors hover:border-brand/40 hover:bg-brand-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
        >
          <Search className="h-4 w-4 shrink-0" />
          <span className="flex-1 truncate text-left">What do you want to accomplish?</span>
          <kbd className="hidden rounded border border-line bg-surface px-1.5 py-0.5 text-micro font-semibold text-fg-muted sm:block">
            Ctrl K
          </kbd>
        </button>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <IconButton
            variant="outline"
            label="Toggle theme"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            <Sun className="hidden h-4 w-4 dark:block" />
            <Moon className="h-4 w-4 dark:hidden" />
          </IconButton>
          <Link
            href="/approvals"
            className={buttonClass({ variant: "outline", iconOnly: true })}
            aria-label="Approvals"
          >
            <Bell className="h-4 w-4" />
            {pending > 0 && (
              <Badge
                tone="danger"
                size="xs"
                className="absolute -right-1 -top-1 h-4 min-w-4 justify-center px-1 py-0 leading-none shadow-card"
              >
                {pending}
              </Badge>
            )}
          </Link>
          <PrimaryLink href="/projects/new" className="hidden sm:inline-flex">
            <Plus className="h-4 w-4" /> New Project
          </PrimaryLink>
          <Avatar name={displayName} hue={217} />
          <BusinessSwitcher />
          {status === "authenticated" && user ? (
            <IconButton
              variant="outline"
              label="Log out"
              title={`Log out ${user.email}`}
              onClick={async () => {
                await logout();
                router.push("/login");
              }}
            >
              <LogOut className="h-4 w-4" />
            </IconButton>
          ) : (
            status !== "unknown" && (
              <Link
                href="/login"
                aria-label="Log in"
                className={buttonClass({ variant: "outline", iconOnly: true })}
              >
                <LogIn className="h-4 w-4" />
              </Link>
            )
          )}
        </div>
      </header>

      <AnimatePresence>
        {menuOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-overlay lg:hidden"
            onClick={() => setMenuOpen(false)}
          >
            <motion.div
              ref={drawerRef}
              initial={{ x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={transition.base}
              onClick={(e) => e.stopPropagation()}
              role="dialog"
              aria-modal="true"
              aria-label="Navigation"
              className="flex h-full w-[260px] flex-col gap-4 overflow-y-auto bg-surface px-3 py-4"
            >
              <div className="flex items-center justify-between px-2">
                <span className="text-body-sm font-bold tracking-wide text-fg">MUSE SPARK</span>
                <IconButton
                  variant="ghost"
                  size="sm"
                  label="Close navigation"
                  onClick={() => setMenuOpen(false)}
                >
                  <X className="h-4 w-4" />
                </IconButton>
              </div>
              <div onClick={() => setMenuOpen(false)}>
                <SidebarLinks />
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
