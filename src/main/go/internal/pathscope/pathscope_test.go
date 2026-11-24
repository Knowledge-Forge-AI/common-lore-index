package pathscope

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestResolveAcceptsRegularGoFileUnderRoot(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "pkg", "file.go")
	mustWriteFile(t, path, "package pkg\n")

	resolved, err := Resolve(root, "pkg/file.go", 1024)

	if err != nil {
		t.Fatalf("Resolve() error = %v", err)
	}
	want, err := filepath.EvalSymlinks(path)
	if err != nil {
		t.Fatal(err)
	}
	if resolved != want {
		t.Fatalf("Resolve() = %q, want %q", resolved, want)
	}
}

func TestResolveRejectsUnsafeOrUnsupportedPathsWithoutLeakingRoot(t *testing.T) {
	root := t.TempDir()
	mustWriteFile(t, filepath.Join(root, "file.txt"), "not go\n")
	if err := os.Mkdir(filepath.Join(root, "dir.go"), 0o755); err != nil {
		t.Fatal(err)
	}

	tests := map[string]string{
		"empty":           "",
		"absolute":        filepath.Join(root, "file.go"),
		"parent escape":   "../file.go",
		"nul":             "bad\x00.go",
		"wrong extension": "file.txt",
		"directory":       "dir.go",
	}
	for name, relative := range tests {
		t.Run(name, func(t *testing.T) {
			_, err := Resolve(root, relative, 1024)
			if err == nil {
				t.Fatal("Resolve() error = nil")
			}
			if strings.Contains(err.Error(), root) {
				t.Fatalf("Resolve() error leaked root: %v", err)
			}
		})
	}
}

func TestResolveRejectsSymlinkEscapeAndOversizedFile(t *testing.T) {
	root := t.TempDir()
	outside := filepath.Join(t.TempDir(), "outside.go")
	mustWriteFile(t, outside, "package outside\n")
	if err := os.Symlink(outside, filepath.Join(root, "link.go")); err != nil {
		t.Fatal(err)
	}
	mustWriteFile(t, filepath.Join(root, "large.go"), "package large\n")

	for _, relative := range []string{"link.go", "large.go"} {
		_, err := Resolve(root, relative, 1)
		if err == nil {
			t.Fatalf("Resolve(%q) error = nil", relative)
		}
		if strings.Contains(err.Error(), root) || strings.Contains(err.Error(), outside) {
			t.Fatalf("Resolve(%q) leaked a path: %v", relative, err)
		}
	}
}

func TestResolveRejectsMissingRootMissingFileAndOversizedPath(t *testing.T) {
	root := t.TempDir()
	tests := []struct {
		root     string
		relative string
	}{
		{root: filepath.Join(root, "missing"), relative: "file.go"},
		{root: root, relative: "missing.go"},
		{root: root, relative: strings.Repeat("x", MaxPathBytes) + ".go"},
	}
	for _, test := range tests {
		if _, err := Resolve(test.root, test.relative, 1024); err == nil {
			t.Fatalf("Resolve(%q) error = nil", test.relative)
		}
	}
}

func mustWriteFile(t *testing.T, path string, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestReadSourceChecksTheSizeAtTheOpenedFile(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "fixture.go")
	const original = "package fixture\n"
	mustWriteFile(t, path, original)
	resolvedRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		t.Fatal(err)
	}
	resolved, err := ResolveFromCanonicalRoot(resolvedRoot, "fixture.go", int64(len(original)))
	if err != nil {
		t.Fatal(err)
	}
	if source, err := ReadSource(resolved, int64(len(original))); err != nil || string(source) != original {
		t.Fatalf("ReadSource() = %q, %v", source, err)
	}
	mustWriteFile(t, path, original+"// growth\n")
	if _, err := ReadSource(resolved, int64(len(original))); !errors.Is(err, ErrFileTooLarge) {
		t.Fatalf("grown source error = %v", err)
	}
}

func TestReadBoundedSourceProbesPastTheAdmittedPrefix(t *testing.T) {
	reader := strings.NewReader("abcd")
	if _, err := readBoundedSource(reader, 3); !errors.Is(err, ErrFileTooLarge) {
		t.Fatalf("readBoundedSource() error = %v", err)
	}
	if reader.Len() != 0 {
		t.Fatalf("reader retained %d bytes", reader.Len())
	}
}
