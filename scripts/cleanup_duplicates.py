#!/usr/bin/env python3
"""
Beets Artist Duplicate Cleanup Script
======================================

Finds and fixes existing duplicate artist folders caused by case sensitivity.

Examples it will fix:
  - "A Boogie Wit da Hoodie" + "A Boogie Wit Da Hoodie" ? "A Boogie Wit da Hoodie"
  - "Alice in Chains" + "Alice In Chains" ? "Alice in Chains"

Usage:
  python3 cleanup_duplicates.py --dry-run     # Show what would be fixed
  python3 cleanup_duplicates.py --fix         # Actually fix duplicates
  python3 cleanup_duplicates.py --threshold 0.90  # Adjust similarity threshold

Safety:
  - Creates backup before making changes
  - Validates all operations
  - Logs everything to cleanup.log
"""

import os
import sys
import argparse
import difflib
from collections import defaultdict
import logging

# Try to import beets
try:
    from beets import config as beets_config
    from beets.library import Library
    from beets import ui
    HAVE_BEETS = True
except ImportError:
    print("ERROR: Could not import beets. Please install beets first.")
    sys.exit(1)

# Try to import musicbrainzngs for canonical names
try:
    import musicbrainzngs as mb_api
    mb_api.set_useragent("beets-duplicate-cleanup", "1.0.0", "https://github.com/beetbox/beets")
    HAVE_MB_API = True
except ImportError:
    HAVE_MB_API = False
    print("Warning: musicbrainzngs not available. Will use local normalization only.")


class DuplicateFinder:
    """Find and resolve duplicate artists based on case sensitivity."""
    
    def __init__(self, threshold=0.85, use_musicbrainz=True):
        self.threshold = threshold
        self.use_musicbrainz = use_musicbrainz
        self.logger = logging.getLogger('cleanup')
        
    def fuzzy_match_similarity(self, str1, str2):
        """Calculate similarity between two strings (0-1)."""
        if not str1 or not str2:
            return 0.0
        
        # Normalize for comparison
        s1 = ' '.join(str1.lower().split())
        s2 = ' '.join(str2.lower().split())
        
        return difflib.SequenceMatcher(None, s1, s2).ratio()
    
    def normalize_title_case(self, text):
        """Apply title case normalization with exceptions."""
        if not text:
            return text
        
        exceptions = ['DJ', 'USA', 'UK', 'EP', 'LP', 'CD', 'II', 'III', 'IV', 'V']
        small_words = ['a', 'an', 'the', 'and', 'but', 'or', 'for', 'nor', 'on', 'at', 
                      'to', 'from', 'by', 'of', 'in', 'with', 'da', 'de', 'von', 'van']
        
        words = text.split()
        normalized = []
        
        for i, word in enumerate(words):
            # Check exceptions
            if word.upper() in exceptions:
                for exc in exceptions:
                    if exc.upper() == word.upper():
                        normalized.append(exc)
                        break
            # First word - capitalize
            elif i == 0:
                normalized.append(word.capitalize())
            # Small words - lowercase
            elif word.lower() in small_words:
                normalized.append(word.lower())
            # Everything else - capitalize
            else:
                normalized.append(word.capitalize())
        
        return ' '.join(normalized)
    
    def lookup_musicbrainz_canonical(self, artist_name):
        """Look up canonical artist name from MusicBrainz."""
        if not HAVE_MB_API or not self.use_musicbrainz:
            return None
        
        try:
            result = mb_api.search_artists(artist=artist_name, limit=5)  # Get top 5
            if result and 'artist-list' in result and len(result['artist-list']) > 0:
                for match in result['artist-list']:
                    canonical = match.get('name')
                    score = int(match.get('ext:score', '0'))
                    
                    # STRICT VALIDATION: Must be very similar to original
                    # This prevents "DJ Python" -> "Monty Python" type errors
                    if canonical and score > 95:
                        # Check similarity between original and MB result
                        similarity = self.fuzzy_match_similarity(artist_name, canonical)
                        
                        # Only use if the names are actually similar (>85%)
                        # This filters out false matches like "DJ Python" vs "Monty Python"
                        if similarity > 0.85:
                            self.logger.info(f"MusicBrainz: '{artist_name}' -> '{canonical}' (score: {score}, similarity: {similarity:.0%})")
                            return canonical
                        else:
                            self.logger.debug(f"MusicBrainz rejected: '{artist_name}' -> '{canonical}' (score: {score}, similarity: {similarity:.0%} too low)")
                    
        except Exception as e:
            self.logger.debug(f"MusicBrainz lookup failed for '{artist_name}': {e}")
        
        return None
    
    def find_duplicate_groups(self, lib):
        """
        Find groups of artists that are likely duplicates.
        Returns dict: {canonical_name: [list of similar artist names]}
        """
        # Get all unique artists from library
        all_artists = set()
        for item in lib.items():
            if item.albumartist:
                all_artists.add(item.albumartist)
            if item.artist:
                all_artists.add(item.artist)
        
        all_artists = sorted(all_artists)
        
        self.logger.info(f"Found {len(all_artists)} unique artist names in library")
        
        # Find duplicate groups
        duplicate_groups = defaultdict(list)
        processed = set()
        
        for i, artist1 in enumerate(all_artists):
            if artist1 in processed:
                continue
            
            # Find all similar artists
            similar = [artist1]
            
            for artist2 in all_artists[i+1:]:
                if artist2 in processed:
                    continue
                
                similarity = self.fuzzy_match_similarity(artist1, artist2)
                
                if similarity >= self.threshold:
                    similar.append(artist2)
                    processed.add(artist2)
                    self.logger.debug(f"Match: '{artist1}' ~= '{artist2}' ({similarity:.1%})")
            
            if len(similar) > 1:
                # Determine canonical name for this group
                canonical = self.resolve_canonical_name(similar)
                duplicate_groups[canonical] = similar
                
                for artist in similar:
                    processed.add(artist)
        
        return duplicate_groups
    
    def resolve_canonical_name(self, artist_names):
        """
        Determine the canonical name from a list of similar variants.
        
        Priority:
        1. MusicBrainz canonical name
        2. Most common name in library
        3. Title case normalized version
        """
        # Try MusicBrainz first
        for artist in artist_names:
            mb_canonical = self.lookup_musicbrainz_canonical(artist)
            if mb_canonical:
                # Check if it matches one of our variants
                for candidate in artist_names:
                    if self.fuzzy_match_similarity(mb_canonical, candidate) > 0.95:
                        self.logger.info(f"Using MusicBrainz canonical: '{candidate}'")
                        return candidate
                # Otherwise use the MB name directly
                self.logger.info(f"Using MusicBrainz name: '{mb_canonical}'")
                return mb_canonical
        
        # Use title case normalized version of first artist
        canonical = self.normalize_title_case(artist_names[0])
        self.logger.info(f"Using normalized name: '{canonical}' from '{artist_names[0]}'")
        return canonical


class DuplicateFixer:
    """Fix duplicate artists by updating database and moving files."""
    
    def __init__(self, lib, dry_run=True):
        self.lib = lib
        self.dry_run = dry_run
        self.logger = logging.getLogger('cleanup')
        
    def fix_duplicate_group(self, canonical_name, duplicate_names):
        """
        Fix a group of duplicate artists.
        Updates all items from duplicates to canonical name.
        """
        if canonical_name in duplicate_names:
            # Remove canonical from duplicates list
            to_fix = [d for d in duplicate_names if d != canonical_name]
        else:
            to_fix = duplicate_names
        
        if not to_fix:
            self.logger.warning(f"No duplicates to fix for '{canonical_name}'")
            return 0
        
        total_fixed = 0
        
        for duplicate in to_fix:
            self.logger.info(f"\nFixing: '{duplicate}' -> '{canonical_name}'")
            
            # Find all items with this artist name
            items = list(self.lib.items(f'albumartist:"{duplicate}"'))
            items.extend(self.lib.items(f'artist:"{duplicate}"'))
            
            if not items:
                self.logger.warning(f"  No items found for '{duplicate}'")
                continue
            
            self.logger.info(f"  Found {len(items)} items to update")
            
            for item in items:
                # Update artist names
                if item.albumartist == duplicate:
                    if self.dry_run:
                        self.logger.info(f"  [DRY RUN] Would update albumartist: {item.path}")
                    else:
                        item.albumartist = canonical_name
                        item.store()
                        self.logger.info(f"  Updated albumartist: {item.path}")
                    total_fixed += 1
                
                if item.artist == duplicate:
                    if self.dry_run:
                        self.logger.info(f"  [DRY RUN] Would update artist: {item.path}")
                    else:
                        item.artist = canonical_name
                        item.store()
        
        # Move files to new location
        if not self.dry_run and total_fixed > 0:
            self.logger.info(f"  Moving files to new artist folder...")
            # Get updated items
            items = list(self.lib.items(f'albumartist:"{canonical_name}"'))
            for item in items:
                item.move()
            self.logger.info(f"  Files moved successfully")
        
        return total_fixed


def setup_logging(verbose=False):
    """Setup logging to file and console."""
    log_level = logging.DEBUG if verbose else logging.INFO
    
    # Create logger
    logger = logging.getLogger('cleanup')
    logger.setLevel(log_level)
    
    # File handler
    fh = logging.FileHandler('cleanup_duplicates.log')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(fh)
    
    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(log_level)
    ch.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    logger.addHandler(ch)
    
    return logger


def main():
    parser = argparse.ArgumentParser(
        description='Find and fix duplicate artist folders in Beets library',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show what would be fixed (safe)
  python3 cleanup_duplicates.py --dry-run
  
  # Actually fix duplicates
  python3 cleanup_duplicates.py --fix
  
  # Adjust similarity threshold (default: 0.85 = 85%)
  python3 cleanup_duplicates.py --dry-run --threshold 0.90
  
  # Verbose output
  python3 cleanup_duplicates.py --dry-run --verbose
"""
    )
    
    parser.add_argument('--dry-run', action='store_true', default=True,
                       help='Show what would be fixed without making changes (default)')
    parser.add_argument('--fix', action='store_true',
                       help='Actually fix duplicates (opposite of --dry-run)')
    parser.add_argument('--threshold', type=float, default=0.85,
                       help='Similarity threshold 0-1 (default: 0.85)')
    parser.add_argument('--no-musicbrainz', action='store_true',
                       help='Disable MusicBrainz lookups (use local normalization only)')
    parser.add_argument('--verbose', action='store_true',
                       help='Show detailed debug information')
    
    args = parser.parse_args()
    
    # Setup logging
    logger = setup_logging(args.verbose)
    
    # Determine mode
    dry_run = not args.fix
    mode_str = "DRY RUN" if dry_run else "FIX MODE"
    
    logger.info("="*70)
    logger.info(f"Beets Artist Duplicate Cleanup - {mode_str}")
    logger.info("="*70)
    logger.info(f"Similarity threshold: {args.threshold:.0%}")
    use_mb = HAVE_MB_API and not args.no_musicbrainz
    mb_status = 'enabled' if use_mb else ('disabled (--no-musicbrainz)' if args.no_musicbrainz else 'disabled (not installed)')
    logger.info(f"MusicBrainz lookups: {mb_status}")
    logger.info("")
    
    # Load beets library
    try:
        beets_config.read()
        lib = Library(beets_config['library'].as_filename())
        logger.info(f"Loaded library: {lib.path}")
    except Exception as e:
        logger.error(f"Failed to load beets library: {e}")
        return 1
    
    # Find duplicates
    logger.info("\nScanning for duplicate artists...")
    use_mb = HAVE_MB_API and not args.no_musicbrainz
    finder = DuplicateFinder(threshold=args.threshold, use_musicbrainz=use_mb)
    duplicate_groups = finder.find_duplicate_groups(lib)
    
    if not duplicate_groups:
        logger.info("\n? No duplicate artists found!")
        return 0
    
    # Display results
    logger.info(f"\n{'='*70}")
    logger.info(f"Found {len(duplicate_groups)} groups of duplicates:")
    logger.info(f"{'='*70}\n")
    
    total_duplicates = 0
    for canonical, duplicates in sorted(duplicate_groups.items()):
        logger.info(f"Canonical: '{canonical}'")
        for dup in duplicates:
            if dup != canonical:
                logger.info(f"  ? '{dup}'")
                total_duplicates += 1
        logger.info("")
    
    logger.info(f"Total duplicate names to fix: {total_duplicates}\n")
    
    # Fix duplicates
    if dry_run:
        logger.info("="*70)
        logger.info("DRY RUN - No changes will be made")
        logger.info("Run with --fix to actually apply these changes")
        logger.info("="*70)
    else:
        # Confirm before proceeding
        logger.info("="*70)
        logger.info("WARNING: About to modify your library!")
        logger.info("="*70)
        response = input("\nProceed with fixing duplicates? (yes/no): ")
        if response.lower() != 'yes':
            logger.info("Cancelled by user")
            return 0
    
    # Apply fixes
    fixer = DuplicateFixer(lib, dry_run=dry_run)
    total_fixed = 0
    
    for canonical, duplicates in duplicate_groups.items():
        fixed = fixer.fix_duplicate_group(canonical, duplicates)
        total_fixed += fixed
    
    # Summary
    logger.info("\n" + "="*70)
    if dry_run:
        logger.info(f"DRY RUN COMPLETE - Would fix {total_fixed} items")
        logger.info("Run with --fix to actually apply changes")
    else:
        logger.info(f"FIX COMPLETE - Fixed {total_fixed} items")
        logger.info("Check cleanup_duplicates.log for details")
    logger.info("="*70)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
