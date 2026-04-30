import React, { useState } from 'react';
import { motion } from 'framer-motion';

export default function Toggle({ label = "SYS_LINK", onToggle }) {
  const [isOn, setIsOn] = useState(false);

  const handleToggle = () => {
    const newState = !isOn;
    setIsOn(newState);
    if (onToggle) onToggle(newState);
  };

  return (
    <div className="flex items-center gap-3 font-mono text-[10px] tracking-widest text-gray-400">
      <span>{label}</span>
      <button
        onClick={handleToggle}
        className={`w-10 h-5 flex items-center rounded-full p-0.5 cursor-pointer transition-colors duration-300 ${
          isOn ? 'bg-white' : 'bg-slate-800 border border-white/10'
        }`}
      >
        <motion.div
          className={`w-4 h-4 rounded-full shadow-md ${
            isOn ? 'bg-slate-950' : 'bg-gray-400'
          }`}
          layout
          transition={{ type: "spring", stiffness: 700, damping: 30 }}
          animate={{ x: isOn ? 20 : 0 }}
        />
      </button>
      <span className={`font-bold transition-colors duration-300 ${isOn ? 'text-white' : 'text-gray-600'}`}>
        {isOn ? 'ON' : 'OFF'}
      </span>
    </div>
  );
}
