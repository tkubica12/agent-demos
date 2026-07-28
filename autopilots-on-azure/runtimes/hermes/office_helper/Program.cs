using System.Text.Json;
using System.Text.RegularExpressions;
using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Validation;
using A = DocumentFormat.OpenXml.Drawing;
using P = DocumentFormat.OpenXml.Presentation;
using W = DocumentFormat.OpenXml.Wordprocessing;

static string Require(string[] args, int index, string name)
{
    if (args.Length <= index || string.IsNullOrWhiteSpace(args[index]))
    {
        throw new ArgumentException($"{name} is required.");
    }
    return args[index];
}

static string RevisionId() =>
    DateTimeOffset.UtcNow.ToUnixTimeMilliseconds().ToString();

static W.InsertedRun TrackedInsertion(W.Run run, string author) =>
    new(run)
    {
        Id = RevisionId(),
        Author = author,
        Date = DateTime.UtcNow,
    };

static void AppendTrackedParagraph(
    string path,
    string text,
    string author)
{
    using var document = WordprocessingDocument.Open(path, true);
    var body = document.MainDocumentPart?.Document.Body
        ?? throw new InvalidDataException("Word document has no body.");
    var run = new W.Run(
        new W.Text(text)
        {
            Space = SpaceProcessingModeValues.Preserve,
        });
    var paragraph = new W.Paragraph(
        TrackedInsertion(run, author));
    var section = body.Elements<W.SectionProperties>().LastOrDefault();
    if (section is null)
    {
        body.Append(paragraph);
    }
    else
    {
        body.InsertBefore(paragraph, section);
    }
    document.MainDocumentPart!.Document.Save();
}

static void ReplaceTrackedText(
    string path,
    string oldText,
    string newText,
    string author)
{
    using var document = WordprocessingDocument.Open(path, true);
    var main = document.MainDocumentPart?.Document
        ?? throw new InvalidDataException("Word document has no main part.");
    var matches = main
        .Descendants<W.Text>()
        .Where(value => value.Text == oldText)
        .ToList();
    if (matches.Count != 1)
    {
        throw new InvalidOperationException(
            $"Exact Word replacement requires one matching text run; found {matches.Count}.");
    }
    var originalText = matches[0];
    var originalRun = originalText.Ancestors<W.Run>().FirstOrDefault()
        ?? throw new InvalidDataException("Matching Word text has no run.");
    var parent = originalRun.Parent
        ?? throw new InvalidDataException("Matching Word run has no parent.");

    var deletedRun = (W.Run)originalRun.CloneNode(true);
    var deletedText = deletedRun
        .Descendants<W.Text>()
        .First(value => value.Text == oldText);
    deletedText.InsertAfterSelf(
        new W.DeletedText(oldText)
        {
            Space = deletedText.Space,
        });
    deletedText.Remove();

    var insertedRun = (W.Run)originalRun.CloneNode(true);
    insertedRun
        .Descendants<W.Text>()
        .First(value => value.Text == oldText)
        .Text = newText;

    var deletion = new W.DeletedRun(deletedRun)
    {
        Id = RevisionId(),
        Author = author,
        Date = DateTime.UtcNow,
    };
    var insertion = TrackedInsertion(insertedRun, author);
    parent.InsertBefore(deletion, originalRun);
    parent.InsertAfter(insertion, deletion);
    originalRun.Remove();
    main.Save();
}

static object InspectWordStructure(string path)
{
    using var document = WordprocessingDocument.Open(path, false);
    var body = document.MainDocumentPart?.Document.Body
        ?? throw new InvalidDataException("Word document has no body.");
    var paragraphs = body
        .Descendants<W.Paragraph>()
        .Take(500)
        .Select((paragraph, paragraphIndex) => new
        {
            ParagraphIndex = paragraphIndex,
            Text = string.Concat(
                paragraph.Descendants<W.Text>()
                    .Select(text => text.Text)),
            TextNodes = paragraph
                .Descendants<W.Text>()
                .Take(100)
                .Select((text, textNodeIndex) => new
                {
                    TextNodeIndex = textNodeIndex,
                    Text = text.Text,
                })
                .ToList(),
        })
        .ToList();
    return new
    {
        Paragraphs = paragraphs,
        TableCount = body.Descendants<W.Table>().Count(),
        ContentControlCount = body.Descendants<W.SdtElement>().Count(),
        LegacyFormFieldCount = body.Descendants<W.FormFieldData>().Count(),
    };
}

static object PatchWordTextNodes(
    string path,
    string editsJson)
{
    using var edits = JsonDocument.Parse(editsJson);
    if (edits.RootElement.ValueKind != JsonValueKind.Array)
    {
        throw new ArgumentException("edits must be a JSON array.");
    }
    using var document = WordprocessingDocument.Open(path, true);
    var main = document.MainDocumentPart?.Document
        ?? throw new InvalidDataException("Word document has no main part.");
    var paragraphs = main.Body?.Descendants<W.Paragraph>().ToList()
        ?? throw new InvalidDataException("Word document has no body.");
    var applied = 0;
    foreach (var edit in edits.RootElement.EnumerateArray())
    {
        var paragraphIndex = edit.GetProperty(
            "paragraphIndex").GetInt32();
        var textNodeIndex = edit.GetProperty(
            "textNodeIndex").GetInt32();
        var searchText = edit.GetProperty(
            "searchText").GetString() ?? "";
        var replacementText = edit.GetProperty(
            "replacementText").GetString() ?? "";
        if (
            paragraphIndex < 0
            || paragraphIndex >= paragraphs.Count)
        {
            throw new ArgumentOutOfRangeException(
                "paragraphIndex");
        }
        var textNodes = paragraphs[paragraphIndex]
            .Descendants<W.Text>()
            .ToList();
        if (
            textNodeIndex < 0
            || textNodeIndex >= textNodes.Count)
        {
            throw new ArgumentOutOfRangeException(
                "textNodeIndex");
        }
        var target = textNodes[textNodeIndex];
        var count = Regex.Matches(
            target.Text ?? "",
            Regex.Escape(searchText)).Count;
        if (count != 1)
        {
            throw new InvalidOperationException(
                $"Expected one match in paragraph {paragraphIndex}, "
                + $"text node {textNodeIndex}; found {count}.");
        }
        target.Text = (target.Text ?? "").Replace(
            searchText,
            replacementText,
            StringComparison.Ordinal);
        if (
            target.Text.StartsWith(' ')
            || target.Text.EndsWith(' '))
        {
            target.Space = SpaceProcessingModeValues.Preserve;
        }
        applied++;
    }
    main.Save();
    return new
    {
        Success = true,
        Applied = applied,
    };
}

static void ReplacePowerPointText(
    string path,
    string oldText,
    string newText)
{
    using var presentation = PresentationDocument.Open(path, true);
    var slides = presentation.PresentationPart?.SlideParts.ToList()
        ?? throw new InvalidDataException("PowerPoint document has no slides.");
    var matches = slides
        .SelectMany(slide => slide.Slide.Descendants<A.Text>())
        .Where(value => value.Text == oldText)
        .ToList();
    if (matches.Count != 1)
    {
        throw new InvalidOperationException(
            $"Exact PowerPoint replacement requires one matching text run; found {matches.Count}.");
    }
    matches[0].Text = newText;
    foreach (var slide in slides)
    {
        slide.Slide.Save();
    }
}

static OpenXmlPackage OpenReadOnly(string path) =>
    Path.GetExtension(path).ToLowerInvariant() switch
    {
        ".docx" => WordprocessingDocument.Open(path, false),
        ".xlsx" => SpreadsheetDocument.Open(path, false),
        ".pptx" => PresentationDocument.Open(path, false),
        _ => throw new ArgumentException(
            "Validation supports DOCX, XLSX, and PPTX files."),
    };

static object ValidateOfficeFile(string path)
{
    using var package = OpenReadOnly(path);
    var errors = new OpenXmlValidator(FileFormatVersions.Microsoft365)
        .Validate(package)
        .Take(50)
        .Select(error => new
        {
            error.Id,
            error.Description,
            Path = error.Path?.XPath,
            Part = error.Part?.Uri.ToString(),
        })
        .ToList();
    return new
    {
        Valid = errors.Count == 0,
        ErrorCount = errors.Count,
        Errors = errors,
    };
}

try
{
    var command = Require(args, 0, "command");
    object result = command switch
    {
        "word-append-tracked" => Run(() => AppendTrackedParagraph(
            Require(args, 1, "path"),
            Require(args, 2, "text"),
            args.Length > 3 ? args[3] : "Hermes")),
        "word-replace-tracked" => Run(() => ReplaceTrackedText(
            Require(args, 1, "path"),
            Require(args, 2, "old text"),
            Require(args, 3, "new text"),
            args.Length > 4 ? args[4] : "Hermes")),
        "word-inspect-structure" => InspectWordStructure(
            Require(args, 1, "path")),
        "word-patch-text-nodes" => PatchWordTextNodes(
            Require(args, 1, "path"),
            Require(args, 2, "edits JSON")),
        "powerpoint-replace-text" => Run(() => ReplacePowerPointText(
            Require(args, 1, "path"),
            Require(args, 2, "old text"),
            Require(args, 3, "new text"))),
        "validate" => ValidateOfficeFile(
            Require(args, 1, "path")),
        _ => throw new ArgumentException(
            $"Unsupported command '{command}'."),
    };
    Console.WriteLine(JsonSerializer.Serialize(result));
    return 0;
}
catch (Exception exception)
{
    Console.Error.WriteLine(
        JsonSerializer.Serialize(
            new
            {
                Error = exception.GetType().Name,
                exception.Message,
            }));
    return 1;
}

static object Run(Action action)
{
    action();
    return new { Success = true };
}
