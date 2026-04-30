import { useRef, useEffect } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import Earth from './Earth';
import OrbitalRings from './OrbitalRings';

export default function EarthSystem({ onNodeClick }) {
  const systemRef = useRef();
  const scrollProgress = useRef(0);
  const targetScrollProgress = useRef(0);

  useEffect(() => {
    const handleScroll = () => {
      const totalScroll = document.documentElement.scrollHeight - window.innerHeight;
      if (totalScroll > 0) {
        targetScrollProgress.current = window.scrollY / totalScroll;
      }
    };

    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  useFrame((state, delta) => {
    if (!systemRef.current) return;

    // Smoothly interpolate scroll progress (lerp)
    scrollProgress.current += (targetScrollProgress.current - scrollProgress.current) * 0.1;

    // Scroll animations (Whole system recedes)
    // 1. Move away (Z position goes from 0 to -35)
    const targetZ = -scrollProgress.current * 40;
    systemRef.current.position.z = THREE.MathUtils.lerp(systemRef.current.position.z, targetZ, 0.1);

    // 2. Scale down (Scale goes from 1 to 0.3)
    const baseScale = 1;
    const targetScale = baseScale * (1 - scrollProgress.current * 0.7);
    systemRef.current.scale.setScalar(THREE.MathUtils.lerp(systemRef.current.scale.x, targetScale, 0.1));

    // 3. Move slightly up/right as it goes back
    const targetX = scrollProgress.current * 6;
    const targetY = scrollProgress.current * 2;
    systemRef.current.position.x = THREE.MathUtils.lerp(systemRef.current.position.x, targetX, 0.1);
    systemRef.current.position.y = THREE.MathUtils.lerp(systemRef.current.position.y, targetY, 0.1);
  });

  return (
    <group ref={systemRef}>
      <Earth />
      <OrbitalRings onNodeClick={onNodeClick} />
    </group>
  );
}
