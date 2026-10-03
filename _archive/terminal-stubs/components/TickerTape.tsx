export default function TickerTape() {
  return (
    <div className="w-full bg-[#121821]/80 backdrop-blur-md border-y border-[#1f2a36] flex items-center overflow-hidden shrink-0 h-7" style={{ WebkitAppRegion: "no-drag" } as any}>
       <div className="flex whitespace-nowrap animate-marquee gap-12 font-mono text-[10px] uppercase font-bold px-4">
          <span className="text-[#e6edf3]">RELIANCE.NS <span className="text-[#3ddc97]">? 1.2%</span></span>
          <span className="text-[#e6edf3]">HDFCBANK.NS <span className="text-[#3ddc97]">? 0.8%</span></span>
          <span className="text-[#e6edf3]">INFY.NS <span className="text-[#ff4d6d]">? 1.1%</span></span>
          <span className="text-[#e6edf3]">TCS.NS <span className="text-[#ff4d6d]">? 0.4%</span></span>
          <span className="text-[#e6edf3]">ITC.NS <span className="text-[#3ddc97]">? 0.1%</span></span>
          <span className="text-[#e6edf3]">BRENT <span className="text-[#ff4d6d]">? 1.4%</span></span>
          <span className="text-[#e6edf3]">USDINR <span className="text-[#3ddc97]">? 0.1%</span></span>
          <span className="text-[#e6edf3]">NIFTY <span className="text-[#ff4d6d]">? 2.1%</span></span>
          <span className="text-[#e6edf3] ml-12">RELIANCE.NS <span className="text-[#3ddc97]">? 1.2%</span></span>
          <span className="text-[#e6edf3]">HDFCBANK.NS <span className="text-[#3ddc97]">? 0.8%</span></span>
          <span className="text-[#e6edf3]">INFY.NS <span className="text-[#ff4d6d]">? 1.1%</span></span>
       </div>
    </div>
  );
}
