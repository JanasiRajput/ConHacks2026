import { useRef, useState, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import { Stars as DreiStars } from '@react-three/drei';
import * as THREE from 'three';

function ShootingStar() {
  const ref = useRef();
  const [speed] = useState(() => Math.random() * 0.5 + 0.5);
  const [direction] = useState(() => {
    // Move from top-right to bottom-left roughly
    return new THREE.Vector3(-1, -0.5, 0).normalize();
  });

  const resetStar = () => {
    if (!ref.current) return;
    // Start somewhere on the right/top
    const x = Math.random() * 50 + 10;
    const y = Math.random() * 30 + 10;
    const z = -Math.random() * 20 - 10;
    ref.current.position.set(x, y, z);
    
    // Random scale/length
    const length = Math.random() * 2 + 1;
    ref.current.scale.set(length, 0.05, 0.05);
    
    // Rotate to match direction
    const angle = Math.atan2(direction.y, direction.x);
    ref.current.rotation.z = angle;
  };

  useMemo(() => {
    // Initial reset
    setTimeout(() => {
      resetStar();
    }, Math.random() * 5000); // Stagger starts
  }, []);

  useFrame((state, delta) => {
    if (!ref.current) return;
    
    // Move
    ref.current.position.addScaledVector(direction, speed);
    
    // If out of bounds, reset
    if (ref.current.position.x < -30 || ref.current.position.y < -30) {
      // Delay before appearing again
      ref.current.position.set(100, 100, 100); // Hide
      if (Math.random() < 0.01) { // Small chance to respawn each frame
        resetStar();
      }
    }
  });

  return (
    <mesh ref={ref}>
      <boxGeometry args={[1, 1, 1]} />
      <meshBasicMaterial 
        color="#ffffff" 
        transparent 
        opacity={0.8} 
        blending={THREE.AdditiveBlending}
      />
    </mesh>
  );
}

export default function SpaceStars() {
  const starsRef = useRef();

  useFrame((state) => {
    if (starsRef.current) {
      starsRef.current.rotation.y = state.clock.getElapsedTime() * 0.01;
      starsRef.current.rotation.x = state.clock.getElapsedTime() * 0.005;
    }
  });

  return (
    <group>
      <group ref={starsRef}>
        <DreiStars 
          radius={300} 
          depth={60} 
          count={5000} 
          factor={7} 
          saturation={0.5} 
          fade 
          speed={1}
        />
      </group>
      {/* Multiple shooting stars */}
      <ShootingStar />
      <ShootingStar />
      <ShootingStar />
      <ShootingStar />
      <ShootingStar />
    </group>
  );
}
