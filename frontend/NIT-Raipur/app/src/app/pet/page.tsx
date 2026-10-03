
"use client";
import { useEffect, useState, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";

export default function PetWindow() {
  const [state, setState] = useState<"idle" | "thinking" | "pointing">("idle");
  const [hovered, setHovered] = useState(false);
  const [alertInfo, setAlertInfo] = useState<{title: string, reason: string} | null>(null);

  // Drag state
  const [isDragging, setIsDragging] = useState(false);
  const dragStart = useRef({ x: 0, y: 0 });
  const hasMoved = useRef(false);

  useEffect(() => {
    if (typeof window !== "undefined" && window.petApi) {
      window.petApi.onShowCopilotThinking(() => {
        setState("thinking");
        setTimeout(() => setState("idle"), 5000);
      });
    }

    const mockInterval = setInterval(() => {
      if (Math.random() > 0.6 && typeof window !== "undefined") {
        setState("pointing");
        setAlertInfo({ 
          title: "Volatility Spike", 
          reason: "Options chain detects unusual put buying in IT sector." 
        });
        if (window.petApi) window.petApi.show();
        
        setTimeout(() => {
          setState("idle");
          setAlertInfo(null);
        }, 8000);
      }
    }, 15000);

    return () => clearInterval(mockInterval);
  }, []);

  useEffect(() => {
    if (typeof window !== "undefined" && window.petApi) {
      // Ignore mouse events if neither hovered nor dragging
      window.petApi.setIgnoreMouse(!(hovered || isDragging));
    }
  }, [hovered, isDragging]);

  const handlePointerDown = (e: React.PointerEvent) => {
    setIsDragging(true);
    hasMoved.current = false;
    dragStart.current = { x: e.screenX, y: e.screenY };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e: React.PointerEvent) => {
    if (!isDragging) return;
    const dx = e.screenX - dragStart.current.x;
    const dy = e.screenY - dragStart.current.y;
    
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) {
      hasMoved.current = true;
      if (window.petApi && window.petApi.moveBy) {
        window.petApi.moveBy(dx, dy);
        dragStart.current = { x: e.screenX, y: e.screenY };
      }
    }
  };

  const handlePointerUp = (e: React.PointerEvent) => {
    setIsDragging(false);
    (e.target as HTMLElement).releasePointerCapture(e.pointerId);
    
    // If it was just a click and didn't move, open terminal
    if (!hasMoved.current) {
      if (window.terminalApi) window.terminalApi.open({});
    }
  };

  return (
    <div className="w-screen h-screen flex flex-col items-center justify-end bg-transparent p-4">
      <div 
        className="relative flex flex-col items-center justify-end group"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <button
          className="absolute top-4 right-4 bg-red-500/80 text-white hover:bg-red-500 rounded-full p-2.5 shadow-[0_0_15px_rgba(239,68,68,0.5)] z-50 cursor-pointer backdrop-blur-sm border border-red-400/50 transition-all duration-200 opacity-0 scale-90 group-hover:opacity-100 group-hover:scale-100 pointer-events-none group-hover:pointer-events-auto"
          onClick={(e) => {
            e.stopPropagation();
            if (window.petApi) window.petApi.hide();
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
        </button>

        <AnimatePresence>
          {state === "pointing" && alertInfo && (
            <motion.div 
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="bg-zinc-800 text-white p-3 rounded-lg shadow-lg mb-2 text-sm max-w-[200px]"
              onClick={() => {
                if(window.terminalApi) window.terminalApi.open({deepLink: "alert"});
              }}
            >
              <div className="font-bold text-red-400">{alertInfo.title}</div>
              <div className="text-zinc-300">{alertInfo.reason}</div>
            </motion.div>
          )}
        </AnimatePresence>

        <div 
          className="relative w-64 h-64 cursor-grab active:cursor-grabbing transition-transform hover:scale-105 select-none"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
        >
          <motion.img 
            src={`/pet/${state}.png`} 
            alt="pet" 
            draggable={false}
            className="absolute inset-0 w-full h-full object-contain drop-shadow-2xl pointer-events-none"
            animate={{ y: [0, -8, 0] }}
            transition={{ repeat: Infinity, duration: 4, ease: "easeInOut" }}
          />
        </div>
      </div>
    </div>
  );
}

