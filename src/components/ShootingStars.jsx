import { useRef, useState } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

function ShootingStar({ onStarClick, index }) {
  const meshRef = useRef();
  const [activeState, setActiveState] = useState('WAITING');
  
  const speed = useRef(1.5 + Math.random() * 1.0);
  const direction = useRef(new THREE.Vector3(1, -0.2, -0.3).normalize());
  const timeAccumulator = useRef(0);
  const waitDelay = useRef(index * 3 + Math.random() * 3); // Stagger initial starts

  const resetPosition = () => {
    if (!meshRef.current) return;
    // Start far left, high up, and deep
    meshRef.current.position.set(
      -30 - Math.random() * 10,
      10 + Math.random() * 10,
      -15 - Math.random() * 10
    );
    
    // Randomize direction slightly for organic feel
    direction.current.set(
      1, 
      -0.1 - Math.random() * 0.2, 
      -0.2 - Math.random() * 0.3
    ).normalize();
    
    // Rotate mesh to align with direction
    // Default cylinder points UP (0,1,0). We need to align it with direction.
    const axis = new THREE.Vector3(0, 1, 0);
    meshRef.current.quaternion.setFromUnitVectors(axis, direction.current);
  };

  useFrame((state, delta) => {
    if (!meshRef.current) return;

    if (activeState === 'WAITING') {
      timeAccumulator.current += delta;
      if (timeAccumulator.current >= waitDelay.current) {
        resetPosition();
        setActiveState('MOVING');
        timeAccumulator.current = 0;
      }
    } else if (activeState === 'MOVING') {
      // Move along direction
      meshRef.current.position.addScaledVector(direction.current, speed.current * delta * 10);

      // Reset if out of bounds (passed the right side of screen)
      if (meshRef.current.position.x > 40 || meshRef.current.position.y < -20) {
        setActiveState('WAITING');
        waitDelay.current = 6 + Math.random() * 4; // Rare: 6-10 seconds wait
        timeAccumulator.current = 0;
      }
    }
  });

  const starData = {
    id: `star_${index}`,
    title: `Cosmic Insight #${index + 1}`,
    description: `A passing celestial body carrying ancient cosmic data. Light years traveled: ${(index + 1) * 1420}`,
    color: '#ffffaa'
  };

  if (activeState === 'WAITING') return null;

  return (
    <mesh
      ref={meshRef}
      onClick={(e) => {
        e.stopPropagation();
        onStarClick(starData);
      }}
      onPointerOver={(e) => {
        e.stopPropagation();
        document.body.style.cursor = 'pointer';
      }}
      onPointerOut={() => {
        document.body.style.cursor = 'auto';
      }}
    >
      <cylinderGeometry args={[0.01, 0.0, 4, 8]} />
      <meshBasicMaterial 
        color="#ffffff" 
        transparent 
        opacity={0.6}
        blending={THREE.AdditiveBlending}
      />
    </mesh>
  );
}

export default function ShootingStars({ onStarClick }) {
  return (
    <group>
      {/* Reduced count to 2 for cinematic rarity */}
      {[...Array(2)].map((_, i) => (
        <ShootingStar key={i} index={i} onStarClick={onStarClick} />
      ))}
    </group>
  );
}
