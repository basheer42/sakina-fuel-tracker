# shipments/utils/ai_order_matcher.py
"""
3-Layer AI Order Matching System for Sakina Gas Company
Handles fuzzy matching of KPC order numbers with local + AI fallback
"""
import os
import json
import difflib
import logging
import time
import re
import requests
from typing import Optional, Tuple, Dict, Any, List
from django.conf import settings
from django.core.cache import cache
from django.apps import apps

logger = logging.getLogger(__name__)

class GroqProvider:
    """Groq AI provider for order number correction (FREE tier: 14,400 requests/day)"""
    
    def __init__(self):
        # Try multiple ways to get the API key
        self.api_key = (
            getattr(settings, 'GROQ_API_KEY', '') or 
            os.environ.get('GROQ_API_KEY', '')
        )
        self.base_url = 'https://api.groq.com/openai/v1/chat/completions'
        # Updated to current available model
        self.model = 'llama-3.1-8b-instant'  # Current free model
        
    def correct_order_number(self, corrupted_order: str, active_orders: List[str]) -> Optional[Tuple[str, float]]:
        """Use Groq AI to correct a corrupted order number."""
        if not self.api_key:
            logger.warning("Groq API key not configured")
            return None
        
        if len(active_orders) > 50:
            # Limit to most recent orders for better API performance
            active_orders = active_orders[:50]
        
        prompt = self._build_correction_prompt(corrupted_order, active_orders)
        
        try:
            response = requests.post(
                self.base_url,
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': self.model,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'temperature': 0.1,  # Low temperature for consistent results
                    'max_tokens': 100,
                },
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content'].strip()
                return self._parse_ai_response(content, active_orders)
            else:
                logger.error(f"Groq API error: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Groq API request failed: {e}")
            return None
    
    def _build_correction_prompt(self, corrupted_order: str, active_orders: List[str]) -> str:
        """Build the correction prompt for Groq AI."""
        orders_list = '\n'.join(f"- {order}" for order in active_orders[:20])
        
        return f"""You are an expert at correcting fuel delivery order numbers that contain OCR errors or typos.

CORRUPTED ORDER: {corrupted_order}

VALID ORDERS TO MATCH AGAINST:
{orders_list}

Common errors include:
- Digit transposition (S00220 → S02020)
- Letter/number confusion (SO2020 → S02020, where O=0)
- Missing/extra characters (502020 → S02020, S020200 → S02020)
- Case variations

Find the BEST matching valid order from the list above.

Respond in this exact format:
MATCH: [order_number]
CONFIDENCE: [0-100]

If no good match exists, respond with:
NO_MATCH
"""
    
    def _parse_ai_response(self, content: str, active_orders: List[str]) -> Optional[Tuple[str, float]]:
        """Parse AI response and validate against active orders"""
        try:
            if "NO_MATCH" in content.upper():
                return None
            
            # Extract match and confidence
            lines = content.split('\n')
            match_line = None
            conf_line = None
            
            for line in lines:
                if 'MATCH:' in line.upper():
                    match_line = line
                elif 'CONFIDENCE:' in line.upper():
                    conf_line = line
            
            if not match_line or not conf_line:
                logger.warning(f"Could not parse Groq response format: {content}")
                return None
            
            # Extract values
            matched_order = match_line.split(':', 1)[1].strip()
            confidence_str = conf_line.split(':', 1)[1].strip()
            
            try:
                confidence = float(confidence_str) / 100.0  # Convert to 0-1 scale
            except ValueError:
                logger.warning(f"Could not parse confidence value: {confidence_str}")
                return None
            
            # Validate that the matched order is actually in our active orders
            if matched_order in active_orders:
                return (matched_order, confidence)
            else:
                logger.warning(f"Groq suggested order '{matched_order}' not in active orders list")
                return None
            
        except Exception as e:
            logger.error(f"Failed to parse Groq response: {e}")
            return None


class IntelligentOrderMatcher:
    """
    AI-powered order matcher with local fuzzy matching + Groq AI fallback.
    """
    
    def __init__(self, local_threshold: float = 0.7, ai_threshold: float = 0.8):
        """
        Initialize the matcher.
        
        Args:
            local_threshold: Minimum similarity for local fuzzy matching
            ai_threshold: Minimum confidence for AI API responses
        """
        self.local_threshold = local_threshold
        self.ai_threshold = ai_threshold
        self.groq_provider = GroqProvider()
        self.cache_timeout = 3600  # 1 hour cache
    
    def normalize_order_number(self, order_no: str) -> str:
        """Normalize order number format for comparison."""
        if not order_no:
            return ""
        
        # Extract only alphanumeric characters and convert to uppercase
        cleaned = re.sub(r'[^A-Z0-9]', '', order_no.upper())
        
        # Must start with S and have digits
        if not cleaned.startswith('S') or len(cleaned) < 3:
            return ""
        
        return cleaned
    
    def calculate_similarity(self, source: str, target: str) -> float:
        """Calculate similarity between two order numbers using multiple methods."""
        source_norm = self.normalize_order_number(source)
        target_norm = self.normalize_order_number(target)
        
        if not source_norm or not target_norm:
            return 0.0
        
        # Method 1: Sequence matching (main method)
        seq_similarity = difflib.SequenceMatcher(None, source_norm, target_norm).ratio()
        
        # Method 2: Character frequency matching (good for transposed digits)
        char_similarity = self._character_frequency_similarity(source_norm, target_norm)
        
        # Method 3: Position-aware matching (rewards correct positions)
        pos_similarity = self._position_aware_similarity(source_norm, target_norm)
        
        # Weighted average - sequence matching gets highest weight
        final_similarity = (seq_similarity * 0.5 + char_similarity * 0.3 + pos_similarity * 0.2)
        
        return final_similarity
    
    def _character_frequency_similarity(self, source: str, target: str) -> float:
        """Calculate similarity based on character frequency (detects transpositions)."""
        if len(source) != len(target):
            return 0.0
        
        source_chars = sorted(source)
        target_chars = sorted(target)
        
        matches = sum(1 for a, b in zip(source_chars, target_chars) if a == b)
        return matches / len(source) if source else 0.0
    
    def _position_aware_similarity(self, source: str, target: str) -> float:
        """Calculate similarity giving higher weight to correct positions."""
        if not source or not target:
            return 0.0
        
        min_len = min(len(source), len(target))
        max_len = max(len(source), len(target))
        
        # Count position matches
        position_matches = sum(1 for i in range(min_len) if source[i] == target[i])
        
        # Penalize length differences
        length_penalty = abs(len(source) - len(target)) / max_len
        
        return (position_matches / max_len) * (1 - length_penalty)
    
    def find_local_best_match(self, parsed_order_no: str, active_orders: List[str]) -> Optional[Tuple[str, float]]:
        """Find best match using local fuzzy matching."""
        if not parsed_order_no or not active_orders:
            return None
        
        best_match = None
        best_score = 0.0
        
        # Debug: Show similarity scores for top matches
        similarities = []
        
        for active_order in active_orders:
            similarity = self.calculate_similarity(parsed_order_no, active_order)
            similarities.append((active_order, similarity))
            
            if similarity > best_score and similarity >= self.local_threshold:
                best_match = active_order
                best_score = similarity
        
        # Debug logging: show top 3 matches
        similarities.sort(key=lambda x: x[1], reverse=True)
        top_matches = similarities[:3]
        logger.info(f"Local matching for '{parsed_order_no}': Top matches: {top_matches}")
        
        if best_match:
            logger.info(f"Local match found: '{parsed_order_no}' → '{best_match}' (score: {best_score:.2f})")
        else:
            logger.info(f"Local matching failed for '{parsed_order_no}' - best score {similarities[0][1]:.2f} below threshold {self.local_threshold}")
        
        return (best_match, best_score) if best_match else None
    
    def find_groq_best_match(self, parsed_order_no: str, active_orders: List[str]) -> Optional[Tuple[str, float]]:
        """Find best match using Groq AI as fallback."""
        # Check cache first
        cache_key = f"groq_order_match_{parsed_order_no}_{hash(tuple(active_orders))}"
        cached_result = cache.get(cache_key)
        if cached_result:
            logger.info(f"Using cached Groq result for {parsed_order_no}")
            return cached_result
        
        try:
            logger.info(f"Trying Groq AI for order correction: {parsed_order_no}")
            
            result = self.groq_provider.correct_order_number(parsed_order_no, active_orders)
            
            if result and result[1] >= self.ai_threshold:
                matched_order, confidence = result
                logger.info(f"Groq found match: {matched_order} (confidence: {confidence:.2f})")
                
                # Cache the successful result
                cache.set(cache_key, result, self.cache_timeout)
                return result
            else:
                logger.info(f"Groq result below threshold or no match for: {parsed_order_no}")
                return None
                
        except Exception as e:
            logger.error(f"Groq AI matching failed: {e}")
            return None
    
    def get_active_order_numbers(self, exclude_statuses: List[str] = None) -> List[str]:
        """Get list of active order numbers that could be matched."""
        if exclude_statuses is None:
            exclude_statuses = ['COMPLETED', 'CANCELLED', 'DELIVERED']
        
        try:
            # Dynamically get Trip model to avoid import issues
            Trip = apps.get_model('shipments', 'Trip')
            
            active_trips = Trip.objects.exclude(
                status__in=exclude_statuses
            ).values_list('kpc_order_number', flat=True)
            
            # Filter out None/empty values and return clean list
            return [order for order in active_trips if order and order.strip()]
        except Exception as e:
            logger.error(f"Error getting active order numbers: {e}")
            return []
    
    def smart_order_lookup(self, parsed_order_no: str) -> Optional[Tuple[Any, Dict[str, Any]]]:
        """
        Intelligent order lookup with fuzzy matching + Groq AI fallback.
        
        Returns:
            Tuple of (Trip object, metadata dict) or None
        """
        # Dynamically get Trip model
        Trip = apps.get_model('shipments', 'Trip')
        
        metadata = {
            'original_order': parsed_order_no,
            'correction_method': None,
            'corrected_order': None,
            'confidence': 0.0,
            'processing_time': 0.0
        }
        
        start_time = time.time()
        
        # Step 1: Try exact match first (fastest)
        try:
            trip = Trip.objects.get(kpc_order_number__iexact=parsed_order_no)
            metadata.update({
                'correction_method': 'exact_match',
                'corrected_order': parsed_order_no,
                'confidence': 1.0,
                'processing_time': time.time() - start_time
            })
            
            logger.info(f"Exact match found for order: {parsed_order_no}")
            return (trip, metadata)
            
        except Trip.DoesNotExist:
            pass
        except Exception as e:
            logger.error(f"Error in exact match lookup: {e}")
        
        # Step 2: Get active orders for matching
        active_orders = self.get_active_order_numbers()
        
        if not active_orders:
            logger.warning("No active orders available for matching")
            metadata['processing_time'] = time.time() - start_time
            return None
        
        logger.info(f"Found {len(active_orders)} active orders for matching")
        
        # Step 3: Try local fuzzy matching
        local_result = self.find_local_best_match(parsed_order_no, active_orders)
        
        if local_result:
            matched_order, confidence = local_result
            
            try:
                trip = Trip.objects.get(kpc_order_number__iexact=matched_order)
                
                metadata.update({
                    'correction_method': 'local_fuzzy_match',
                    'corrected_order': matched_order,
                    'confidence': confidence,
                    'processing_time': time.time() - start_time
                })
                
                logger.info(
                    f"Local fuzzy match: '{parsed_order_no}' → '{matched_order}' "
                    f"(confidence: {confidence:.2f})"
                )
                
                return (trip, metadata)
                
            except Trip.DoesNotExist:
                logger.error(f"Local match found but trip doesn't exist: {matched_order}")
            except Exception as e:
                logger.error(f"Error getting trip for local match: {e}")
        
        # Step 4: Try Groq AI as fallback
        logger.info(f"Local matching failed for {parsed_order_no}, trying Groq AI...")
        
        groq_result = self.find_groq_best_match(parsed_order_no, active_orders)
        
        if groq_result:
            matched_order, confidence = groq_result
            
            try:
                trip = Trip.objects.get(kpc_order_number__iexact=matched_order)
                
                metadata.update({
                    'correction_method': 'groq_ai_correction',
                    'corrected_order': matched_order,
                    'confidence': confidence,
                    'processing_time': time.time() - start_time
                })
                
                logger.info(
                    f"Groq AI correction: '{parsed_order_no}' → '{matched_order}' "
                    f"(confidence: {confidence:.2f})"
                )
                
                return (trip, metadata)
                
            except Trip.DoesNotExist:
                logger.error(f"Groq match found but trip doesn't exist: {matched_order}")
            except Exception as e:
                logger.error(f"Error getting trip for Groq match: {e}")
        
        # Step 5: No match found
        metadata['processing_time'] = time.time() - start_time
        logger.warning(
            f"No match found for order '{parsed_order_no}' after trying all methods "
            f"(processed in {metadata['processing_time']:.2f}s)"
        )
        
        return None


# Simple helper function for your email processing
def get_trip_with_smart_matching(parsed_order_no: str) -> Optional[Tuple[Any, Dict[str, Any]]]:
    """
    Easy-to-use function for smart order matching.
    
    Usage in your email processing:
    result = get_trip_with_smart_matching(kpc_order_number)
    if result:
        trip, metadata = result
        # Continue with your processing
    else:
        # No match found
        logger.error(f"No trip found for {kpc_order_number}")
    """
    if not getattr(settings, 'AI_MATCHER_ENABLED', False):
        logger.info("AI matcher disabled, falling back to standard lookup")
        try:
            Trip = apps.get_model('shipments', 'Trip')
            trip = Trip.objects.get(kpc_order_number__iexact=parsed_order_no)
            metadata = {
                'original_order': parsed_order_no,
                'correction_method': 'exact_match',
                'corrected_order': parsed_order_no,
                'confidence': 1.0,
                'processing_time': 0.0
            }
            return (trip, metadata)
        except Trip.DoesNotExist:
            return None
    
    matcher = IntelligentOrderMatcher(
        local_threshold=getattr(settings, 'AI_MATCHER_LOCAL_THRESHOLD', 0.7),
        ai_threshold=getattr(settings, 'AI_MATCHER_AI_THRESHOLD', 0.8)
    )
    
    return matcher.smart_order_lookup(parsed_order_no)